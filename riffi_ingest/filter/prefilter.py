"""Which stories go to the AI pass. An AI pass takes at most 400 stories and the wider net holds a few
thousand, so the stories are ranked by the part of R that needs no AI (R0) and each category sends only its
best ones (its prefilter_cap in categories.yaml), with every High-priority Bengaluru story always kept.

This module only chooses and explains; `ai-batch` (a protected file) still picks by the old keyword score
until proposal 4b lets it follow this list. `filter_report.md` shows how many of the chosen stories the AI
pass has actually reached."""

from __future__ import annotations

from dataclasses import dataclass

from ..taxonomy import Taxonomy
from .config import FilterConfig
from .model import Assessed
from .relevance import relevance


@dataclass
class Prefilter:
    chosen: list[Assessed]
    left_out: int


def _r0(a: Assessed, cfg: FilterConfig) -> int:
    r0, _ = relevance(a.story.headline, a.topic, None, cfg)
    return r0


def choose_for_ai(assessed: list[Assessed], taxonomy: Taxonomy, cfg: FilterConfig) -> Prefilter:
    pre = cfg["prefilter"]
    cap_total = int(pre["max_stories"])
    keep = pre.get("always_keep") or {}
    ranked = sorted(assessed, key=lambda a: (-_r0(a, cfg), -a.story.source_count, a.story.story_id))
    per_cat: dict[str, int] = {}
    chosen: list[Assessed] = []
    always = [a for a in ranked if a.category == keep.get("category") and a.topic.priority == keep.get("priority")]
    for a in always[:cap_total]:  # the AI pass takes at most max_stories, even for the stories always kept
        chosen.append(a)
        per_cat[a.category] = per_cat.get(a.category, 0) + 1
    taken = {a.story.story_id for a in chosen}
    for a in ranked:
        if len(chosen) >= cap_total:
            break
        if a.story.story_id in taken:
            continue
        rule = taxonomy.categories.get(a.category)
        if rule is not None and per_cat.get(a.category, 0) >= rule.prefilter_cap:
            continue
        chosen.append(a)
        taken.add(a.story.story_id)
        per_cat[a.category] = per_cat.get(a.category, 0) + 1
    return Prefilter(chosen=chosen, left_out=len(assessed) - len(chosen))
