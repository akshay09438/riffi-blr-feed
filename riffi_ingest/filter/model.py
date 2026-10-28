"""The stories the filter looks at, and what it concludes about each."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime

from ..db.connection import from_iso
from ..db.store import AI_CHECKED_SQL
from ..outputs import queries
from ..taxonomy import TopicInfo
from .facts import Facts

STORY_CAP = 20000  # a window never holds more than a few thousand stories; this only bounds a mistake


@dataclass
class StoryView:
    story_id: str
    headline: str
    started_at: datetime
    first_seen_at: datetime
    source_count: int
    sources: list[str]
    links: list[str]
    topic_ids: list[str]  # by the scoring rule: the AI's tags while its verdict is current, else the keyword pass's
    all_topic_ids: list[str]  # every tag of either method; only the safety state reads it
    ai_checked: bool  # an AI verdict that is current (or an exclusion)
    excluded: bool
    excluded_reason: str
    whats_new: str
    ai_angle: str
    facts: Facts | None = None  # the AI pass's extra judgements (sidecar); None = ai missing


@dataclass
class Assessed:
    story: StoryView
    topic: TopicInfo  # the best topic
    category: str
    need_id: str
    safety_state: str  # cleared | editor_only | pending (excluded stories are never assessed)
    r_parts: dict[str, int]
    k_parts: dict[str, int]
    r: int
    k: int
    p: float
    ai_missing: bool
    gate1: bool
    gate2: bool
    picked: bool = False
    tier: str = ""
    reason: str = ""  # why it was not picked, or "picked"
    prefilter: float = field(default=0.0)


def load_stories(conn: sqlite3.Connection, w: queries.Window, max_links: int = 3) -> list[StoryView]:
    """The window's stories (a report fetched in it), with their topics, sources and up to `max_links` links,
    in four queries however many stories there are."""
    start, end = w.bounds
    rows = conn.execute(
        f"WITH {queries.TOUCHED} SELECT c.cluster_id, c.headline, c.first_seen_at, c.started_at, c.source_count,"
        " c.sources, v.excluded, v.excluded_reason, v.whats_new, v.debate_angle,"
        f" (v.cluster_id IS NOT NULL AND {AI_CHECKED_SQL}) AS ai_checked"
        " FROM touched JOIN story_clusters c ON c.cluster_id = touched.cluster_id"
        " LEFT JOIN ai_verdicts v ON v.cluster_id = c.cluster_id ORDER BY c.cluster_id LIMIT ?",
        (start, end, STORY_CAP + 1),
    ).fetchall()
    if len(rows) > STORY_CAP:
        raise ValueError(
            f"the window holds more than {STORY_CAP:,} stories; refusing to cut the list silently."
            " Rebuild a shorter window or raise STORY_CAP in riffi_ingest/filter/model.py"
        )
    ids = [r["cluster_id"] for r in rows]
    names = {r["source_id"]: r["name"] for r in conn.execute("SELECT source_id, name FROM sources")}
    topics = queries.story_topics(conn, ids)
    every: dict[str, set[str]] = {i: set() for i in ids}
    if ids:
        for r in conn.execute(
            "SELECT cluster_id, topic_id FROM item_topics WHERE cluster_id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        ):
            every[r["cluster_id"]].add(r["topic_id"])
    links: dict[str, list[str]] = {i: [] for i in ids}
    if ids:
        for r in conn.execute(
            "SELECT cluster_id, url FROM (SELECT i.cluster_id, i.url, ROW_NUMBER() OVER (PARTITION BY i.cluster_id"
            "   ORDER BY i.url_resolved DESC, i.published_at, i.item_id) AS n FROM items i"
            "   WHERE i.cluster_id IN (SELECT value FROM json_each(?))) WHERE n <= ?",
            (json.dumps(ids), max_links),
        ):
            links[r["cluster_id"]].append(r["url"])
    out = []
    for r in rows:
        source_ids = sorted(json.loads(r["sources"] or "[]"))
        out.append(
            StoryView(
                story_id=r["cluster_id"],
                headline=r["headline"],
                started_at=from_iso(r["started_at"]),
                first_seen_at=from_iso(r["first_seen_at"]),
                source_count=r["source_count"],
                sources=[names.get(s, s) for s in source_ids],
                links=links[r["cluster_id"]],
                topic_ids=sorted({t["topic_id"] for t in topics[r["cluster_id"]]}),
                all_topic_ids=sorted(every[r["cluster_id"]]),
                ai_checked=bool(r["ai_checked"]),
                excluded=bool(r["excluded"]),
                excluded_reason=r["excluded_reason"] or "",
                whats_new=r["whats_new"] or "",
                ai_angle=r["debate_angle"] or "",
            )
        )
    return out
