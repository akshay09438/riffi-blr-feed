"""The AI pass's extra judgements per story (PRD section 8.6), read from a JSON sidecar until the database
carries them (proposal 4d): reports/YYYY-MM-DD/story_facts_v2.json, keyed by story_id.

    {"c-1a2b3c": {"why_care": "...", "daily_life": 14, "culture": 6, "audience_fit": 7, "routine": false,
                  "side_a": "...", "side_b": "...", "hook": "...", "hook_score": 12, "visual": 9,
                  "format": "hot-take reel"}}

The file is written by a Claude Code session, so it is untrusted input: every number is clamped to its range,
every text is cut to one line of plain characters and a length limit, and an entry that is not an object is
ignored. It carries no safety information: exclusion comes only from the database's AI verdicts (Gate 0).
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path

FORMATS = ("explainer reel", "hot-take reel", "poll", "carousel", "text post", "meme")
KEEP_FORMAT = {"‌", "‍"}  # Kannada needs the joiners
MAX_FILE_BYTES = 5 * 1024 * 1024
TEXT_LIMITS = {"why_care": 160, "side_a": 90, "side_b": 90, "hook": 120}


@dataclass(frozen=True)
class Facts:
    why_care: str = ""
    daily_life: int = 0
    culture: int = 0
    audience_fit: int = 0
    routine: bool = False
    side_a: str = ""
    side_b: str = ""
    hook: str = ""
    hook_score: int = 0
    visual: int = 0
    format: str = ""

    @property
    def two_sided(self) -> bool:
        return bool(self.side_a and self.side_b)

    @property
    def one_sided(self) -> bool:
        return bool(self.side_a or self.side_b) and not self.two_sided


def _text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    kept = "".join(
        ch
        for ch in value
        if (unicodedata.category(ch) != "Cc" or ch.isspace())
        and (unicodedata.category(ch) != "Cf" or ch in KEEP_FORMAT)
    )
    return " ".join(kept.split())[:limit]


def _int(value, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(low, min(high, int(value)))


def clean(entry: dict) -> Facts:
    fmt = _text(entry.get("format"), 30).lower()
    return Facts(
        why_care=_text(entry.get("why_care"), TEXT_LIMITS["why_care"]),
        daily_life=_int(entry.get("daily_life"), 0, 20),
        culture=_int(entry.get("culture"), 0, 15),
        audience_fit=_int(entry.get("audience_fit"), -15, 10),
        routine=entry.get("routine") is True,
        side_a=_text(entry.get("side_a"), TEXT_LIMITS["side_a"]),
        side_b=_text(entry.get("side_b"), TEXT_LIMITS["side_b"]),
        hook=_text(entry.get("hook"), TEXT_LIMITS["hook"]),
        hook_score=_int(entry.get("hook_score"), 0, 20),
        visual=_int(entry.get("visual"), 0, 15),
        format=fmt if fmt in FORMATS else "",
    )


def load_facts(path: str | Path) -> tuple[dict[str, Facts], list[str]]:
    """Facts by story_id, and the problems found. A missing file is not a problem: it just means the AI pass
    has not written extra judgements yet (every story is then 'ai missing')."""
    path = Path(path)
    if not path.exists():
        return {}, []
    if path.stat().st_size > MAX_FILE_BYTES:
        return {}, [f"{path.name} is over {MAX_FILE_BYTES // (1024 * 1024)} MB: ignored"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, RecursionError, UnicodeDecodeError) as exc:
        return {}, [f"{path.name} is not valid JSON ({exc}): ignored"]
    if isinstance(raw, dict) and isinstance(raw.get("stories"), dict):
        raw = raw["stories"]
    if not isinstance(raw, dict):
        return {}, [f"{path.name} must be an object keyed by story_id: ignored"]
    facts, problems = {}, []
    for story_id, entry in raw.items():
        if not isinstance(entry, dict):
            problems.append(f"{story_id}: not an object, ignored")
            continue
        facts[str(story_id)] = clean(entry)
    return facts, problems
