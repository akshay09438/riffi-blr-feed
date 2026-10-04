"""Step 5 (BRIEF.md "Relevance score", "STEP 5: SCORE"): a 0-100 score and a label for each story.

Every weight lives in config/scoring.yaml. A story scores by its best topic (priority + geography), the
number of distinct sources carrying it, the best source tier among them, and - once the AI pass has run
- whether it is a new development and has a debate angle. Until then those AI points are 0 and the story
is marked `awaiting_ai` (open question 1). Excluded topics are Drop whatever the score; sensitive topics
keep their score but carry the sensitive flag for a human to check the framing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LABEL_ORDER = ("High", "Medium", "Low")


@dataclass
class TopicFacts:
    topic_id: str
    priority: str = ""
    geography: str = ""
    sensitive: bool = False


@dataclass
class StoryFacts:
    topics: list[TopicFacts] = field(default_factory=list)
    source_tiers: list[str] = field(default_factory=list)  # one per distinct source
    new_development: bool | None = None  # None: the AI pass has not run on this story
    debate_angle: bool | None = None
    excluded: bool = False


@dataclass
class Score:
    total: int
    label: str
    sensitive: bool
    awaiting_ai: bool
    parts: dict[str, int] = field(default_factory=dict)  # factor -> points, for explaining a score
    best_topic: str | None = None


def _first_place(geography: str) -> str:
    """'Pan-India (Bangalore angle)' -> 'Pan-India'; 'Bangalore / Pan-India' -> 'Bangalore'."""
    return re.split(r"\s*[/(]", geography or "", maxsplit=1)[0].strip()


def _kind(tier: str) -> str:
    """'Media (Kannada)' -> 'Media'."""
    return re.split(r"\s*\(", tier or "", maxsplit=1)[0].strip()


class Scorer:
    def __init__(self, weights: dict):
        self.w = weights

    @classmethod
    def load(cls, path: str | Path) -> Scorer:
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

    def geography_points(self, geography: str) -> int:
        place = _first_place(geography)
        place = self.w.get("geography_aliases", {}).get(place, place)
        return int(self.w["geography"].get(place, 0))

    def priority_points(self, priority: str) -> int:
        return int(self.w["priority"].get((priority or "").strip(), 0))

    def tier_points(self, tiers: list[str]) -> int:
        return max((int(self.w["tier"].get(_kind(t), 0)) for t in tiers), default=0)

    def spread_points(self, sources: int) -> int:
        spread = self.w["source_spread"]
        if sources >= 3:
            return int(spread["3+"])
        return int(spread.get(str(sources), 0))

    def label(self, total: int) -> str:
        for name in LABEL_ORDER:
            if total >= int(self.w["labels"][name]):
                return name
        return "Drop"

    def score(self, story: StoryFacts) -> Score:
        best, best_points = None, (0, 0)
        for t in story.topics:
            points = (self.priority_points(t.priority), self.geography_points(t.geography))
            if sum(points) > sum(best_points):
                best, best_points = t, points
        parts = {
            "priority": best_points[0],
            "geography": best_points[1],
            "new_development": int(self.w["new_development"]) if story.new_development else 0,
            "debate_angle": int(self.w["debate_angle"]) if story.debate_angle else 0,
            "source_spread": self.spread_points(len(story.source_tiers)),
            "tier": self.tier_points(story.source_tiers),
        }
        total = min(100, sum(parts.values()))
        return Score(
            total=total,
            label="Drop" if story.excluded else self.label(total),
            sensitive=any(t.sensitive for t in story.topics),
            awaiting_ai=story.new_development is None,
            parts=parts,
            best_topic=best.topic_id if best else None,
        )
