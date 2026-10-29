"""Category -> need -> topic (D-017, PRD section 4): the 18 Riffi content categories, the 75 information
needs in `needs.csv`, and which topic in `topics.csv` serves which need.

`topics.csv` carries three extra columns for this: `need_ids` (";"-separated), `editor_only` ("yes" or empty)
and `old_category`. The importer reads topics.csv by column name, so the extra columns change nothing there.
A source's needs are listed in its `notes` in `feeds.csv` ("needs: BLR1; BLR3").

`validate()` returns the problems as plain sentences, so a bad edit to a CSV is caught by a test and by
`python -m riffi_ingest` users alike, before any run.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

NEEDS_IN_NOTES = re.compile(r"needs:\s*([A-Z]{3}\d+(?:\s*;\s*[A-Z]{3}\d+)*)")


@dataclass(frozen=True)
class CategoryRule:
    name: str
    quota_min: int
    quota_max: int
    prefilter_cap: int
    editor_only_until_safety: bool
    relevance_pass: float | None = None  # this category's own Gate 1 pass mark (filter.yaml otherwise)


@dataclass(frozen=True)
class Need:
    need_id: str
    category: str
    need: str
    what_counts: str


@dataclass(frozen=True)
class TopicInfo:
    topic_id: str
    category: str
    need_ids: tuple[str, ...]
    editor_only: bool
    priority: str = ""
    geography: str = ""
    title: str = ""


def split_ids(text: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in (text or "").replace(",", ";").split(";") if x.strip())


def needs_in_notes(notes: str) -> tuple[str, ...]:
    m = NEEDS_IN_NOTES.search(notes or "")
    return split_ids(m.group(1)) if m else ()


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Taxonomy:
    def __init__(self, categories: dict[str, CategoryRule], needs: dict[str, Need], topics: dict[str, TopicInfo]):
        self.categories = categories
        self.needs = needs
        self.topics = topics

    @classmethod
    def load(cls, categories_yaml: str | Path, needs_csv: str | Path, topics_csv: str | Path) -> Taxonomy:
        data = yaml.safe_load(Path(categories_yaml).read_text(encoding="utf-8")) or {}
        defaults = data.get("defaults") or {}
        categories = {}
        for name, over in (data.get("categories") or {}).items():
            conf = {**defaults, **(over or {})}
            categories[name] = CategoryRule(
                name=name,
                quota_min=int(conf["quota_min"]),
                quota_max=int(conf["quota_max"]),
                prefilter_cap=int(conf["prefilter_cap"]),
                editor_only_until_safety=bool(conf["editor_only_until_safety"]),
                relevance_pass=float(conf["relevance_pass"]) if conf.get("relevance_pass") is not None else None,
            )
        needs = {
            r["need_id"].strip(): Need(
                r["need_id"].strip(), r["category"].strip(), r["need"].strip(), r.get("what counts", "").strip()
            )
            for r in _read(Path(needs_csv))
        }
        topics = {
            r["topic_id"].strip(): TopicInfo(
                topic_id=r["topic_id"].strip(),
                category=(r.get("category") or "").strip(),
                need_ids=split_ids(r.get("need_ids", "")),
                editor_only=(r.get("editor_only") or "").strip().lower() == "yes",
                priority=(r.get("priority") or "").strip(),
                geography=(r.get("geography") or "").strip(),
                title=(r.get("topic") or "").strip(),
            )
            for r in _read(Path(topics_csv))
        }
        return cls(categories, needs, topics)

    def category_of_topic(self, topic_id: str) -> str | None:
        t = self.topics.get(topic_id)
        return t.category if t else None

    def needs_of_topic(self, topic_id: str) -> tuple[str, ...]:
        t = self.topics.get(topic_id)
        return t.need_ids if t else ()

    def topics_for_need(self, need_id: str) -> list[str]:
        return sorted(t.topic_id for t in self.topics.values() if need_id in t.need_ids)

    def editor_only_topics(self) -> set[str]:
        return {t.topic_id for t in self.topics.values() if t.editor_only}

    def validate(self, feeds_csv: str | Path | None = None) -> list[str]:
        """Every problem found, as a sentence. Empty means the three files agree."""
        problems: list[str] = []
        for need in self.needs.values():
            if need.category not in self.categories:
                problems.append(f"need {need.need_id} has the unknown category '{need.category}'")
        for cat in self.categories:
            if not any(n.category == cat for n in self.needs.values()):
                problems.append(f"category '{cat}' has no information need in needs.csv")
        for t in self.topics.values():
            if t.category not in self.categories:
                problems.append(f"topic {t.topic_id} has the unknown category '{t.category}'")
            if not t.need_ids:
                problems.append(f"topic {t.topic_id} serves no need (need_ids is empty)")
            for n in t.need_ids:
                if n not in self.needs:
                    problems.append(f"topic {t.topic_id} names the unknown need {n}")
        for need in self.needs.values():
            if not self.topics_for_need(need.need_id):
                problems.append(f"need {need.need_id} ({need.need}) has no topic")
        if feeds_csv is not None:
            served: dict[str, int] = {}
            for r in _read(Path(feeds_csv)):
                if r.get("route_type") == "Manual":
                    continue
                for n in needs_in_notes(r.get("notes", "")):
                    served[n] = served.get(n, 0) + 1
                    if n not in self.needs:
                        problems.append(f"source {r['source_id']} names the unknown need {n}")
                if r.get("category", "").strip() not in self.categories:
                    problems.append(f"source {r['source_id']} has the unknown category '{r.get('category')}'")
            for need in self.needs.values():
                if not served.get(need.need_id):
                    problems.append(f"need {need.need_id} ({need.need}) has no source")
        return problems
