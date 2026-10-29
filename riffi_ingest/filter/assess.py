"""Gates 0, 1 and 2 for every story of a window: who is excluded, and R, K and P for the rest."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..taxonomy import Taxonomy
from .config import FilterConfig
from .content import content
from .facts import Facts
from .framing import advice_problem
from .model import Assessed, StoryView
from .relevance import best_topic, relevance, topic_points
from .safety_state import safety_state
from .sources_check import source_problem


@dataclass
class Assessment:
    assessed: list[Assessed] = field(default_factory=list)
    excluded: Counter = field(default_factory=Counter)  # excluded_reason -> stories (never listed by headline)
    no_topic: int = 0  # stories with no tracked topic: not candidates
    total: int = 0


def assess(
    stories: list[StoryView],
    facts: dict[str, Facts],
    taxonomy: Taxonomy,
    cfg: FilterConfig,
    *,
    now: datetime,
    gate0: bool,
) -> Assessment:
    out = Assessment(total=len(stories))
    w = cfg["picks"]["weights"]
    pass_ok, pass_missing = float(cfg["content"]["pass"]), float(cfg["content"]["pass_ai_missing"])
    max_age = timedelta(hours=float(cfg["freshness"]["max_age_hours"]))
    editor_topics = taxonomy.editor_only_topics()
    pass_r, pass_r_missing = float(cfg["relevance"]["pass"]), float(cfg["relevance"]["pass_ai_missing"])
    for story in stories:
        if story.excluded:
            out.excluded[story.excluded_reason or "no reason given"] += 1
            continue
        topics = [taxonomy.topics[t] for t in story.topic_ids if t in taxonomy.topics]
        top = best_topic(topics, cfg)
        if top is None:
            out.no_topic += 1
            continue
        story.facts = facts.get(story.story_id)
        r, r_parts = relevance(story.headline, top, story.facts, cfg)
        k, k_parts = content(
            started_at=story.started_at,
            source_count=story.source_count,
            facts=story.facts,
            ai_angle=story.ai_angle,
            topic_has_angles=True,
            sides_withheld=bool(set(story.all_topic_ids) & editor_topics),
            now=now,
            cfg=cfg,
        )
        missing = story.facts is None
        category = taxonomy.categories.get(top.category)
        own = category.relevance_pass if category is not None else None
        bar_r = own if own is not None else pass_r
        bar_r_missing = min(own, pass_r_missing) if own is not None else pass_r_missing
        out.assessed.append(
            Assessed(
                story=story,
                topic=top,
                category=top.category,
                need_id=top.need_ids[0] if top.need_ids else "",
                safety_state=safety_state(story, category, taxonomy, gate0=gate0),
                r_parts=r_parts,
                k_parts=k_parts,
                r=r,
                k=k,
                p=round(float(w["R"]) * r + float(w["K"]) * k, 1),
                ai_missing=missing,
                fresh=now - story.started_at <= max_age,
                framing_problem=advice_problem(top.category, story.facts, cfg, story.headline),
                source_problem=source_problem(story.links, cfg),
                sides_withheld=bool(set(story.all_topic_ids) & editor_topics),
                gate1=r >= (bar_r_missing if missing else bar_r),
                gate2=k >= (pass_missing if missing else pass_ok),
                prefilter=float(topic_points(top, cfg)),
            )
        )
    return out
