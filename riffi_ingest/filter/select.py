"""Selection: the daily shortlist. Final pick score P = 0.55 R + 0.45 K (weights in config); a story needs
both gates and P at or above the Medium tier. The best are taken under the quotas in categories.yaml (each
category's minimum first, then the best remaining up to the target), at most `max_per_topic` per topic.
Ties: newer first, then more sources, then story id, so the same input always gives the same picks."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..taxonomy import Taxonomy
from .config import FilterConfig
from .model import Assessed
from .similar import near_duplicates


@dataclass
class Selection:
    picks: list[Assessed]
    notes: list[str]  # plain-sentence remarks for the run summary


def _order(a: Assessed):
    return (-a.p, -a.story.started_at.timestamp(), -a.story.source_count, a.story.story_id)


def _tier(p: float, cfg: FilterConfig) -> str:
    t = cfg["picks"]["tiers"]
    if p >= t["High"]:
        return "High"
    return "Medium" if p >= t["Medium"] else "Preview"  # Preview: only a story with no AI judgement can be here


def select(assessed: list[Assessed], taxonomy: Taxonomy, cfg: FilterConfig) -> Selection:
    pk = cfg["picks"]
    target, per_topic_max = int(pk["target"]), int(pk["max_per_topic"])
    floor = float(pk["tiers"]["Medium"])
    floor_missing = float(pk["floor_ai_missing"])

    def floor_for(a: Assessed) -> float:
        return floor_missing if a.ai_missing else floor

    for a in assessed:
        if not a.gate1:
            own = getattr(taxonomy.categories.get(a.category), "relevance_pass", None)
            bar = cfg["relevance"]["pass_ai_missing"] if a.ai_missing else cfg["relevance"]["pass"]
            if own is not None:
                bar = min(own, bar) if a.ai_missing else own
            a.reason = f"relevance {a.r} is below the pass mark {bar}" + (" (no AI judgement)" if a.ai_missing else "")
        elif not a.gate2:
            bar = cfg["content"]["pass_ai_missing"] if a.ai_missing else cfg["content"]["pass"]
            a.reason = f"content score {a.k} is below the pass mark {bar}" + (
                " (no AI judgement)" if a.ai_missing else ""
            )
        elif not a.fresh:
            a.reason = f"first reported more than {cfg['freshness']['max_age_hours']:g} hours ago"
        elif a.framing_problem:
            a.reason = f"not picked: {a.framing_problem}"
        elif a.source_problem:
            a.reason = f"not picked: {a.source_problem}"
        elif a.p < floor_for(a):
            a.reason = f"pick score {a.p} is below {floor_for(a):g}" + (" (no AI judgement)" if a.ai_missing else "")
    eligible = sorted(
        (
            a
            for a in assessed
            if a.gate1
            and a.gate2
            and a.fresh
            and not a.framing_problem
            and not a.source_problem
            and a.p >= floor_for(a)
        ),
        key=_order,
    )
    dropped = near_duplicates(eligible, cfg)
    for a in eligible:
        if a.story.story_id in dropped:
            keep = dropped[a.story.story_id]
            a.reason = f"near-duplicate of {keep}, which has more sources or a better score"
    eligible = [a for a in eligible if a.story.story_id not in dropped]
    for a in eligible:
        a.eligible = True
    picks: list[Assessed] = []
    per_cat: Counter = Counter()
    per_topic: Counter = Counter()

    def room(a: Assessed) -> bool:
        rule = taxonomy.categories.get(a.category)
        cap = rule.quota_max if rule else 0
        return per_cat[a.category] < cap and per_topic[a.topic.topic_id] < per_topic_max

    def take(a: Assessed) -> None:
        picks.append(a)
        a.picked = True
        a.reason = "picked"
        per_cat[a.category] += 1
        per_topic[a.topic.topic_id] += 1

    for name, rule in taxonomy.categories.items():  # each category's minimum first
        for a in eligible:
            if per_cat[name] >= rule.quota_min or len(picks) >= target:
                break
            if a.category == name and not a.picked and room(a):
                take(a)
    for a in eligible:
        if len(picks) >= target:
            break
        if not a.picked and room(a):
            take(a)
    for a in eligible:
        if a.picked:
            continue
        rule = taxonomy.categories.get(a.category)
        if len(picks) >= target:
            a.reason = f"the day's {target} picks were already filled by higher scores"
        elif rule is not None and per_cat[a.category] >= rule.quota_max:
            a.reason = f"{a.category} already has its {rule.quota_max} picks for the day"
        else:
            a.reason = f"topic {a.topic.topic_id} already has {per_topic_max} picks"
    for a in picks:
        a.tier = _tier(a.p, cfg)
    picks.sort(key=_order)
    notes = []
    if len(picks) < int(pk["min_to_publish"]):
        g1 = sum(not a.gate1 for a in assessed)
        g2 = sum(a.gate1 and not a.gate2 for a in assessed)
        notes.append(
            f"Only {len(picks)} stories passed (fewer than {pk['min_to_publish']}); nothing was added to fill the list."
            f" {g1} stories failed relevance and {g2} more failed content-ability."
        )
    return Selection(picks, notes)
