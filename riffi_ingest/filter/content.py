"""Gate 2: can we make something from it? A content score K (0-100): is there a real debate, a hook for the
first line of a 30-second video, is it fresh, do several sources carry it, can it be shown or explained
simply. Without the AI's judgement the debate comes from the topic's own debate angles (topics.csv) and the
hook and visual points are 0, so such a story has a lower bar (content.pass_ai_missing)."""

from __future__ import annotations

from datetime import datetime, timedelta

from .config import FilterConfig
from .facts import Facts


def freshness_points(started_at: datetime, now: datetime, cfg: FilterConfig) -> int:
    c = cfg["content"]
    age = now - started_at
    if age <= timedelta(hours=24):
        return int(c["fresh_24h"])
    if age <= timedelta(hours=48):
        return int(c["fresh_48h"])
    return 0


def momentum_points(source_count: int, cfg: FilterConfig) -> int:
    c = cfg["content"]
    return int(c["sources_3plus"]) if source_count >= 3 else int(c["sources_2"]) if source_count == 2 else 0


def content(
    *,
    started_at: datetime,
    source_count: int,
    facts: Facts | None,
    ai_angle: str,
    topic_has_angles: bool,
    now: datetime,
    cfg: FilterConfig,
) -> tuple[int, dict[str, int]]:
    c = cfg["content"]
    if facts is not None and facts.two_sided:
        debate = int(c["debate_two_sided"])
    elif (facts is not None and facts.one_sided) or ai_angle:
        debate = int(c["debate_one_sided"])
    elif facts is None and topic_has_angles:
        debate = int(c["debate_one_sided"])  # the topic's general angles, as the digest shows them (D-012)
    else:
        debate = 0
    parts = {
        "debate": debate,
        "hook": min(facts.hook_score, int(c["hook_max"])) if facts is not None else 0,
        "freshness": freshness_points(started_at, now, cfg),
        "momentum": momentum_points(source_count, cfg),
        "visual": min(facts.visual, int(c["visual_max"])) if facts is not None else 0,
    }
    return max(0, min(100, sum(parts.values()))), parts
