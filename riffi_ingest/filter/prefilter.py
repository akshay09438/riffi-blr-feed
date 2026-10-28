"""Which stories go to the AI pass (and, for the extras, to `extras-batch`). An AI pass takes at most 400 stories
and the wider net holds a few thousand, so each category sends only its best ones (its prefilter_cap in
categories.yaml), ranked by the part of the score that needs no AI: R without the AI points, plus freshness and
the number of sources. Three rules, in this order:

1. every category that has stories gets at least `prefilter.min_per_category` of its best (fewer if it has fewer),
   so a category with modest topic points is never starved by the big ones;
2. every High-priority Bangalore story is kept, up to the category's cap and the total budget;
3. the rest of the budget goes to the best remaining stories overall, never past a category's cap.

The total never passes `prefilter.max_stories`. This module only chooses and explains; `ai-batch` (a protected
file) still picks by the old keyword score; `extras-batch` follows this list. `filter_report.md` shows how many
of the chosen stories the AI pass has actually reached."""

from __future__ import annotations

from dataclasses import dataclass

from ..taxonomy import Taxonomy
from .config import FilterConfig
from .content import freshness_points, momentum_points
from .model import Assessed
from .relevance import relevance


@dataclass
class Prefilter:
    chosen: list[Assessed]
    left_out: int
    per_category: dict[str, int]


def rank_key(a: Assessed, cfg: FilterConfig, now) -> tuple:
    r0, _ = relevance(a.story.headline, a.topic, None, cfg)
    score = r0 + freshness_points(a.story.started_at, now, cfg) + momentum_points(a.story.source_count, cfg)
    return (-score, -a.story.source_count, a.story.story_id)


def choose_for_ai(assessed: list[Assessed], taxonomy: Taxonomy, cfg: FilterConfig, now=None) -> Prefilter:
    pre = cfg["prefilter"]
    budget = int(pre["max_stories"])
    minimum = int(pre["min_per_category"])
    keep = pre.get("always_keep") or {}
    now = now or max((a.story.started_at for a in assessed), default=None)
    ranked = sorted(assessed, key=lambda a: rank_key(a, cfg, now)) if assessed else []

    def cap_of(category: str) -> int:
        rule = taxonomy.categories.get(category)
        return rule.prefilter_cap if rule is not None else 0

    chosen: list[Assessed] = []
    taken: set[str] = set()
    per: dict[str, int] = {}

    def take(a: Assessed) -> bool:
        if len(chosen) >= budget or a.story.story_id in taken or per.get(a.category, 0) >= cap_of(a.category):
            return False
        chosen.append(a)
        taken.add(a.story.story_id)
        per[a.category] = per.get(a.category, 0) + 1
        return True

    for name in taxonomy.categories:  # 1. each category's minimum
        n = 0
        for a in ranked:
            if n >= minimum:
                break
            if a.category == name and take(a):
                n += 1
    for a in ranked:  # 2. High-priority Bangalore stories
        if a.category == keep.get("category") and a.topic.priority == keep.get("priority"):
            take(a)
    for a in ranked:  # 3. the best of the rest
        take(a)
    return Prefilter(chosen=chosen, left_out=len(assessed) - len(chosen), per_category=per)
