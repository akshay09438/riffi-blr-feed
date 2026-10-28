"""config/filter.yaml read into one object, with the checks that make a typo stop the run instead of quietly
changing what is picked."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class FilterConfig:
    raw: dict

    @classmethod
    def load(cls, path: str | Path) -> FilterConfig:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for section, keys in {
            "relevance": (
                "pass",
                "pass_ai_missing",
                "priority",
                "need_match",
                "geography",
                "ai_max",
                "ai_min",
                "routine_penalty",
                "fallback_audience",
            ),
            "content": (
                "pass",
                "pass_ai_missing",
                "debate_two_sided",
                "debate_one_sided",
                "fresh_24h",
                "fresh_48h",
                "sources_3plus",
                "sources_2",
                "hook_max",
                "visual_max",
            ),
            "prefilter": ("max_stories", "min_per_category"),
            "freshness": ("max_age_hours",),
            "similar": (
                "jaccard",
                "same_day_shared_entities",
                "same_day_shared_words",
                "jaccard_floor",
                "generic_entities",
                "generic_words",
            ),
            "framing": ("advice_categories", "advice_patterns"),
            "picks": ("target", "min_to_publish", "weights", "tiers", "floor_ai_missing", "max_per_topic", "max_links"),
        }.items():
            block = data.get(section)
            if not isinstance(block, dict):
                raise ValueError(f"filter.yaml: the '{section}' section is missing")
            for key in keys:
                if key not in block:
                    raise ValueError(f"filter.yaml: '{section}.{key}' is missing")
        for path, keys in {
            ("relevance", "priority"): ("High", "Medium", "Low"),
            ("relevance", "ai_max"): ("daily_life", "culture", "audience_fit"),
            ("relevance", "ai_min"): ("audience_fit",),
            ("relevance", "fallback_audience"): ("boost", "penalty"),
            ("picks", "weights"): ("R", "K"),
        }.items():
            block = data[path[0]][path[1]]
            if not isinstance(block, dict) or any(k not in block for k in keys):
                raise ValueError(f"filter.yaml: '{'.'.join(path)}' needs {', '.join(keys)}")
        if not {"High", "Medium"} <= set(data["picks"]["tiers"]):
            raise ValueError("filter.yaml: picks.tiers needs High and Medium")
        if data["picks"]["tiers"]["High"] < data["picks"]["tiers"]["Medium"]:
            raise ValueError("filter.yaml: picks.tiers.High must not be below Medium")
        return cls(data)

    def __getitem__(self, key: str):
        return self.raw[key]

    def get(self, key: str, default=None):
        return self.raw.get(key, default)

    def terms(self, key: str) -> re.Pattern | None:
        """The audience boost / penalty terms as one whole-word, case-insensitive pattern (None: no terms)."""
        return _terms_pattern(self.raw.get("audience", {}).get(key) or [])

    def routine_pattern(self) -> re.Pattern | None:
        return _terms_pattern(self.raw["relevance"].get("routine_terms") or [])


def _terms_pattern(terms: list) -> re.Pattern | None:
    words = [str(t).strip() for t in terms if str(t).strip()]
    if not words:
        return None
    body = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
    return re.compile(rf"(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])", re.IGNORECASE)
