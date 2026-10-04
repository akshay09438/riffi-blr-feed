"""Step 4, part 1: the keyword pass (BRIEF.md "Tagging", point 1).

Each topic in config/topic_keywords.yaml has plain keyword phrases (not regexes, so the team can tune
them safely). A story is a candidate for a topic when any keyword appears in its title or summary:
- case-insensitive; any run of whitespace in a phrase matches any whitespace;
- English (Latin-script) keywords match whole words only, so "fare" does not match "welfare";
- Kannada keywords match at the start of a word only, because Kannada adds suffixes to the stem
  ("ಬೆಂಗಳೂರ" matches "ಬೆಂಗಳೂರಿನಲ್ಲಿ"). `\\b` cannot be used: it does not work inside Kannada script;
- `local: true` topics count only when the text also names Bengaluru / Karnataka or a local body
  (the file's `local_context` list), for topics whose keywords are generic ("property tax");
- `exclude` phrases veto a topic (known false positives).

This pass favours recall: the AI pass (step 4, part 2) confirms or rejects its candidates. Stories that
name Bengaluru / Karnataka but match no topic are "unmatched local", for a person to review weekly
(BRIEF.md point 3).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

KANNADA_RE = re.compile(r"[ಀ-೿]")
LATIN_EDGE = r"A-Za-z0-9À-ɏ"  # letters and digits that may not touch an English keyword
KANNADA_EDGE = r"ಀ-೿"


def _phrase_pattern(phrase: str) -> str:
    words = phrase.strip().split()
    body = r"\s+".join(re.escape(w) for w in words)
    if KANNADA_RE.search(phrase):
        return rf"(?<![{KANNADA_EDGE}]){body}"  # start of a word; Kannada suffixes may follow
    start = rf"(?<![{LATIN_EDGE}])" if re.match(r"\w", words[0]) else ""
    end = rf"(?![{LATIN_EDGE}])" if re.search(r"\w$", words[-1]) else ""
    return start + body + end


def compile_phrases(phrases: list[str]) -> re.Pattern | None:
    """One case-insensitive pattern matching any phrase; the matched text tells which one."""
    # longest first, so "fare hike" is reported rather than the "fare" inside it
    ordered = sorted({p.strip() for p in phrases if p and p.strip()}, key=len, reverse=True)
    parts = [_phrase_pattern(p) for p in ordered]
    return re.compile("|".join(f"(?:{p})" for p in parts), re.I) if parts else None


@dataclass
class KeywordTopic:
    topic_id: str
    label: str
    keywords: list[str]
    local: bool = False
    exclude: list[str] = field(default_factory=list)
    pattern: re.Pattern | None = None
    exclude_pattern: re.Pattern | None = None

    def __post_init__(self):
        self.pattern = compile_phrases(self.keywords)
        self.exclude_pattern = compile_phrases(self.exclude)


@dataclass
class TagResult:
    topics: dict[str, list[str]] = field(default_factory=dict)  # topic_id -> the keywords that matched
    local: bool = False  # the text names Bengaluru / Karnataka or a local body

    @property
    def unmatched_local(self) -> bool:
        return self.local and not self.topics


class KeywordTagger:
    def __init__(self, topics: list[KeywordTopic], local_context: list[str]):
        self.topics = topics
        self.local_pattern = compile_phrases(local_context)

    @classmethod
    def load(cls, path: str | Path) -> KeywordTagger:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        topics = [
            KeywordTopic(
                topic_id=str(tid),
                label=str(t.get("label", "")),
                keywords=[str(k) for k in t.get("keywords") or []],
                local=bool(t.get("local", False)),
                exclude=[str(x) for x in t.get("exclude") or []],
            )
            for tid, t in (data.get("topics") or {}).items()
        ]
        return cls(topics, [str(x) for x in data.get("local_context") or []])

    def tag(self, text: str) -> TagResult:
        text = text or ""
        local = bool(self.local_pattern and self.local_pattern.search(text))
        result = TagResult(local=local)
        for t in self.topics:
            if t.pattern is None:
                continue
            hits = sorted({m.group(0).lower() for m in t.pattern.finditer(text)})
            if not hits or (t.local and not local):
                continue
            if t.exclude_pattern and t.exclude_pattern.search(text):
                continue
            result.topics[t.topic_id] = hits
        return result

    def tag_texts(self, texts: list[str]) -> TagResult:
        """Tag a story from all its texts (every member's title and summary) at once, so a local-only
        topic counts even when the place name is in one report and the keyword in another."""
        return self.tag("\n".join(t for t in texts if t))


def check_against_topics_csv(tagger: KeywordTagger, topics_csv: str | Path) -> list[str]:
    """Problems a person should fix: topics.csv ids with no keywords, keyword ids not in topics.csv."""
    with open(topics_csv, encoding="utf-8", newline="") as f:
        csv_ids = [row["topic_id"].strip() for row in csv.DictReader(f)]
    kw_ids = {t.topic_id for t in tagger.topics if t.keywords}
    problems = [f"{tid}: in topics.csv but has no keywords" for tid in csv_ids if tid not in kw_ids]
    problems += [
        f"{t.topic_id}: has keywords but is not in topics.csv" for t in tagger.topics if t.topic_id not in csv_ids
    ]
    return problems
