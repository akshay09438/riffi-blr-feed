"""Seed sources from feeds.csv and topics from topics.csv (BRIEF.md step 6). Re-importing is idempotent:
rows are updated in place, fetch history and health columns are kept, and a source that has left the
CSV is marked inactive, never deleted (its history belongs to the two-week test)."""

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..sources import load_sources
from .connection import utc_iso


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    deactivated: int = 0


SOURCE_COLUMNS = (
    "name", "category", "route_type", "fetch_url", "link_or_handle", "topics_hint", "tier", "priority_x_feed",
    "backup_google_news_url", "known_status", "notes",
)  # fmt: skip
# The statements are written out in full (no string building), so it is plain to see that only values,
# never text from the CSVs, reach SQL.
UPDATE_SOURCE = (
    "UPDATE sources SET name = ?, category = ?, route_type = ?, fetch_url = ?, link_or_handle = ?, topics_hint = ?,"
    " tier = ?, priority_x_feed = ?, backup_google_news_url = ?, known_status = ?, notes = ?, active = 1,"
    " imported_at = ? WHERE source_id = ?"
)
INSERT_SOURCE = (
    "INSERT INTO sources (source_id, name, category, route_type, fetch_url, link_or_handle, topics_hint, tier,"
    " priority_x_feed, backup_google_news_url, known_status, notes, imported_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
UPSERT_TOPIC = (
    "INSERT INTO topics (topic_id, type, topic, date_or_window, category, geography, status, why_people_talk,"
    " debate_angles, spice, noise, priority, sensitive_note, imported_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(topic_id) DO UPDATE SET"
    " type = excluded.type, topic = excluded.topic, date_or_window = excluded.date_or_window,"
    " category = excluded.category, geography = excluded.geography, status = excluded.status,"
    " why_people_talk = excluded.why_people_talk, debate_angles = excluded.debate_angles, spice = excluded.spice,"
    " noise = excluded.noise, priority = excluded.priority, sensitive_note = excluded.sensitive_note,"
    " imported_at = excluded.imported_at"
)
TOPIC_COLUMNS = (
    "type", "topic", "date_or_window", "category", "geography", "status", "why_people_talk", "debate_angles",
    "spice", "noise", "priority", "sensitive_note",
)  # fmt: skip


def import_sources(conn: sqlite3.Connection, feeds_csv: str | Path, now: datetime | None = None) -> ImportResult:
    stamp = utc_iso(now or datetime.now(timezone.utc))
    result = ImportResult()
    existing = {r["source_id"] for r in conn.execute("SELECT source_id FROM sources")}
    seen = set()
    with conn:
        for s in load_sources(feeds_csv):
            seen.add(s.source_id)
            values = [getattr(s, c) if c != "priority_x_feed" else int(s.priority_x_feed) for c in SOURCE_COLUMNS]
            if s.source_id in existing:
                conn.execute(UPDATE_SOURCE, (*values, stamp, s.source_id))
                result.updated += 1
            else:
                conn.execute(INSERT_SOURCE, (s.source_id, *values, stamp))
                result.added += 1
        for gone in existing - seen:
            cur = conn.execute("UPDATE sources SET active = 0 WHERE source_id = ? AND active = 1", (gone,))
            result.deactivated += cur.rowcount
    return result


def _int_or_none(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def import_topics(conn: sqlite3.Connection, topics_csv: str | Path, now: datetime | None = None) -> ImportResult:
    stamp = utc_iso(now or datetime.now(timezone.utc))
    result = ImportResult()
    existing = {r["topic_id"] for r in conn.execute("SELECT topic_id FROM topics")}
    with open(topics_csv, encoding="utf-8", newline="") as f, conn:
        for row in csv.DictReader(f):
            tid = row["topic_id"].strip()
            values = [
                _int_or_none(row.get(c, "")) if c in ("spice", "noise") else (row.get(c) or "").strip()
                for c in TOPIC_COLUMNS
            ]
            conn.execute(UPSERT_TOPIC, (tid, *values, stamp))
            if tid in existing:
                result.updated += 1
            else:
                result.added += 1
    return result
