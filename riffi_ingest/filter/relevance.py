"""Gate 1: would our user care? A relevance score R (0-100) from the story's best topic, the places it
touches, and - when the AI pass has judged the story - how much it changes the user's daily life, how much
their circle will talk about it, how well it fits an 18-28 year old tier-1 urban reader, and whether it is
routine official activity. Without the AI's judgement, a few audience terms stand in for it."""

from __future__ import annotations

import re

from ..taxonomy import TopicInfo
from .config import FilterConfig
from .facts import Facts


def first_place(geography: str) -> str:
    """'Pan-India (Bangalore angle)' -> 'Pan-India'; 'Bangalore / Pan-India' -> 'Bangalore'."""
    return re.split(r"\s*[/(]", geography or "", maxsplit=1)[0].strip()


def geography_points(geography: str, cfg: FilterConfig) -> int:
    rel = cfg["relevance"]
    place = first_place(geography)
    place = rel.get("geography_aliases", {}).get(place, place)
    return int(rel["geography"].get(place, 0))


def priority_points(priority: str, cfg: FilterConfig) -> int:
    return int(cfg["relevance"]["priority"].get((priority or "").strip(), 0))


def topic_points(topic: TopicInfo, cfg: FilterConfig) -> int:
    """How strongly a topic backs a story: priority plus geography. The best topic of a story is the one with
    the most (ties: the lower topic id), so the choice never depends on the order of a list."""
    return priority_points(topic.priority, cfg) + geography_points(topic.geography, cfg)


def best_topic(topics: list[TopicInfo], cfg: FilterConfig) -> TopicInfo | None:
    return min(topics, key=lambda t: (-topic_points(t, cfg), t.topic_id)) if topics else None


def relevance(headline: str, topic: TopicInfo, facts: Facts | None, cfg: FilterConfig) -> tuple[int, dict[str, int]]:
    rel = cfg["relevance"]
    parts = {
        "priority": priority_points(topic.priority, cfg),
        "need_match": int(rel["need_match"]) if topic.need_ids else 0,
        "geography": geography_points(topic.geography, cfg),
    }
    boost, penalty = cfg.terms("boost_terms"), cfg.terms("penalty_terms")
    if facts is not None:
        cap = rel["ai_max"]
        parts["daily_life"] = min(facts.daily_life, int(cap["daily_life"]))
        parts["culture"] = min(facts.culture, int(cap["culture"]))
        parts["audience_fit"] = max(
            int(rel["ai_min"]["audience_fit"]), min(facts.audience_fit, int(cap["audience_fit"]))
        )
        routine = facts.routine
    else:
        fb = rel["fallback_audience"]
        fit = 0
        if boost and boost.search(headline or ""):
            fit += int(fb["boost"])
        if penalty and penalty.search(headline or ""):
            fit += int(fb["penalty"])
        parts["audience_fit"] = fit
        pattern = cfg.routine_pattern()
        routine = bool(pattern and pattern.search(headline or ""))
    parts["routine"] = int(rel["routine_penalty"]) if routine else 0
    return max(0, min(100, sum(parts.values()))), parts
