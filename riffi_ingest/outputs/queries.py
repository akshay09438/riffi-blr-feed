"""The read-only questions the digest and the health report ask the database (step 8, D-012).

Every statement here is a SELECT with `?` values only, and every list is bounded (a LIMIT, or one row per
topic or source), so the reports stay quick as items and stories grow by thousands a day.

The window is chosen on `items.fetched_at` (indexed), not on `story_clusters.updated_at` (not indexed, and
rewritten whenever a story grows): a story is "in the window" when at least one of its reports was fetched in
it. Times are compared as stored text: ISO-8601 in UTC (`connection.utc_iso`), where text order is time order.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from ..db.connection import from_iso, utc_iso
from ..db.store import AI_CHECKED_SQL
from ..fetchers.parse import IST

DAY = timedelta(hours=24)
DIGEST_HOUR = time(7, 0)  # BRIEF.md: the digest is due at 07:00 IST; --date regenerates the 24 h ending then
QUIET_AFTER = timedelta(days=7)  # a High-priority topic with nothing for this long is called out

# Topics follow the scoring rule (store.story_facts): the AI's tags while the story's AI verdict is current
# (none, if it found none), else the keyword pass's. `it` is the item_topics row being tested.
TOPIC_RULE = (
    "it.match_method = CASE WHEN EXISTS (SELECT 1 FROM ai_verdicts v WHERE v.cluster_id = it.cluster_id"
    f" AND {AI_CHECKED_SQL}) THEN 'llm' ELSE 'keyword' END"
)
TOUCHED = (
    "touched AS (SELECT DISTINCT cluster_id FROM items"
    " WHERE fetched_at >= ? AND fetched_at < ? AND cluster_id IS NOT NULL)"
)
PRIORITY_ORDER = "CASE t.priority WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 WHEN 'Low' THEN 2 ELSE 3 END"


@dataclass(frozen=True)
class Window:
    """[start, end) in UTC, and the IST date that names the report folder."""

    start: datetime
    end: datetime
    day: date

    @property
    def bounds(self) -> tuple[str, str]:
        return utc_iso(self.start), utc_iso(self.end)


def window_for(now: datetime, day: date | None = None) -> Window:
    """D-012: by default the 24 hours before `now`, filed under today's IST date. With `day` (to regenerate a
    past digest), the 24 hours ending 07:00 IST on that day."""
    if day is None:
        return Window(now - DAY, now, now.astimezone(IST).date())
    end = datetime.combine(day, DIGEST_HOUR, tzinfo=IST)
    return Window(end - DAY, end, day)


@dataclass
class Counts:
    items: int = 0
    stories: int = 0
    new_stories: int = 0
    awaiting_ai: int = 0
    labels: dict[str, int] = field(default_factory=dict)  # High / Medium / Low / Drop / Unscored -> stories


def has_items(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM items)").fetchone()[0] == 1


def counts(conn: sqlite3.Connection, w: Window) -> Counts:
    """Items fetched in the window; the stories they belong to, how many began in it, their labels, and how many
    still wait for the AI pass."""
    start, end = w.bounds
    out = Counts(
        items=conn.execute(
            "SELECT COUNT(*) FROM items WHERE fetched_at >= ? AND fetched_at < ?", (start, end)
        ).fetchone()[0]
    )
    rows = conn.execute(
        f"WITH {TOUCHED} SELECT COALESCE(c.label, 'Unscored') AS label, COUNT(*) AS n,"
        " SUM(c.first_seen_at >= ? AND c.first_seen_at < ?) AS new,"
        f" SUM(NOT EXISTS (SELECT 1 FROM ai_verdicts v WHERE v.cluster_id = c.cluster_id AND {AI_CHECKED_SQL}))"
        "   AS awaiting"
        " FROM touched JOIN story_clusters c ON c.cluster_id = touched.cluster_id GROUP BY 1",
        (start, end, start, end),
    )
    for r in rows:
        out.labels[r["label"]] = r["n"]
        out.stories += r["n"]
        out.new_stories += r["new"] or 0
        out.awaiting_ai += r["awaiting"] or 0
    return out


def ranked_stories(conn: sqlite3.Connection, w: Window, limit: int) -> list[sqlite3.Row]:
    """Stories in the window, best first, Drop left out (counted in `counts`, never listed). The link is the
    first report's, preferring one resolved to the publisher; source names follow source_id order."""
    start, end = w.bounds
    return conn.execute(
        f"WITH {TOUCHED} SELECT c.cluster_id, c.headline, c.relevance_score, c.label, c.sensitive,"
        " c.source_count, c.started_at,"
        f" (v.cluster_id IS NOT NULL AND {AI_CHECKED_SQL}) AS ai_checked, v.whats_new AS ai_whats_new,"
        " v.debate_angle AS ai_angle,"
        " (SELECT i.url FROM items i WHERE i.cluster_id = c.cluster_id"
        "   ORDER BY i.url_resolved DESC, i.published_at, i.item_id LIMIT 1) AS url,"
        " (SELECT group_concat(name, '; ') FROM (SELECT s.name FROM json_each(c.sources) j"
        "   JOIN sources s ON s.source_id = j.value ORDER BY s.source_id)) AS source_names"
        " FROM touched JOIN story_clusters c ON c.cluster_id = touched.cluster_id"
        " LEFT JOIN ai_verdicts v ON v.cluster_id = c.cluster_id"
        " WHERE COALESCE(c.label, '') <> 'Drop'"
        " ORDER BY c.relevance_score IS NULL, c.relevance_score DESC, c.source_count DESC, c.started_at DESC,"
        " c.cluster_id LIMIT ?",
        (start, end, limit),
    ).fetchall()


def story_topics(conn: sqlite3.Connection, cluster_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
    """Each story's topics (by the scoring rule) with the topic's name, priority, geography and debate angles."""
    out: dict[str, list[sqlite3.Row]] = {cid: [] for cid in cluster_ids}
    if not cluster_ids:
        return out
    rows = conn.execute(
        "SELECT it.cluster_id, it.topic_id, t.topic, t.priority, t.geography, t.debate_angles"
        " FROM item_topics it LEFT JOIN topics t ON t.topic_id = it.topic_id"
        f" WHERE it.cluster_id IN (SELECT value FROM json_each(?)) AND {TOPIC_RULE}"
        " ORDER BY it.cluster_id, it.topic_id",
        (json.dumps(cluster_ids),),
    )
    for r in rows:
        out[r["cluster_id"]].append(r)
    return out


def topics_moved(conn: sqlite3.Connection, w: Window, limit: int = 200) -> list[sqlite3.Row]:
    """Topics with at least one non-Drop story in the window: how many stories, and the best one's headline.
    High-priority topics first."""
    start, end = w.bounds
    return conn.execute(
        f"WITH {TOUCHED}, tagged AS (SELECT it.topic_id, c.cluster_id, c.headline, c.relevance_score,"
        "   c.source_count FROM touched JOIN story_clusters c ON c.cluster_id = touched.cluster_id"
        f"   JOIN item_topics it ON it.cluster_id = c.cluster_id WHERE COALESCE(c.label, '') <> 'Drop' AND {TOPIC_RULE}),"
        " ranked AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY topic_id ORDER BY relevance_score DESC,"
        "   source_count DESC, cluster_id) AS n, COUNT(*) OVER (PARTITION BY topic_id) AS stories FROM tagged)"
        " SELECT r.topic_id, t.topic, t.priority, r.stories, r.headline FROM ranked r"
        " LEFT JOIN topics t ON t.topic_id = r.topic_id WHERE r.n = 1"
        f" ORDER BY {PRIORITY_ORDER}, r.stories DESC, r.topic_id LIMIT ?",
        (start, end, limit),
    ).fetchall()


def quiet_high_topics(conn: sqlite3.Connection, end: datetime) -> list[sqlite3.Row]:
    """High-priority topics with no story that began in the 7 days before `end`, with when one last began
    (None: never). One index lookup per topic (item_topics_topic), so it does not scan every story."""
    return conn.execute(
        "SELECT * FROM (SELECT t.topic_id, t.topic, (SELECT MAX(c.started_at) FROM item_topics it"
        "   JOIN story_clusters c ON c.cluster_id = it.cluster_id"
        f"   WHERE it.topic_id = t.topic_id AND c.started_at < ? AND {TOPIC_RULE}) AS last_seen"
        "   FROM topics t WHERE t.priority = 'High')"
        " WHERE last_seen IS NULL OR last_seen < ? ORDER BY last_seen IS NOT NULL, last_seen, topic_id",
        (utc_iso(end), utc_iso(end - QUIET_AFTER)),
    ).fetchall()


def history_start(conn: sqlite3.Connection) -> datetime | None:
    """When the engine's history begins: the first diary row or the first fetched item, whichever is earlier."""
    row = conn.execute(
        "SELECT (SELECT MIN(started_at) FROM engine_runs) AS runs, (SELECT MIN(fetched_at) FROM items) AS items"
    ).fetchone()
    stamps = [from_iso(s) for s in (row["runs"], row["items"]) if s]
    return min(stamps) if stamps else None


def source_health(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every active source's health columns (kept up to date by each fetch), in source_id order."""
    return conn.execute(
        "SELECT source_id, name, route_type, last_status, last_ok_at, newest_item_at, fields_present,"
        " consecutive_failures FROM sources WHERE active = 1 ORDER BY source_id"
    ).fetchall()


def tagged_stories_by_source(conn: sqlite3.Connection, since: datetime, until: datetime) -> dict[str, int]:
    """BRIEF.md "Useful": how many distinct topic-tagged stories each source's items (fetched in the period)
    belong to. A source missing from the result had none."""
    rows = conn.execute(
        "SELECT i.source_id, COUNT(DISTINCT i.cluster_id) AS n FROM items i"
        " WHERE i.fetched_at >= ? AND i.fetched_at < ?"
        " AND EXISTS (SELECT 1 FROM item_topics it WHERE it.cluster_id = i.cluster_id) GROUP BY i.source_id",
        (utc_iso(since), utc_iso(until)),
    )
    return {r["source_id"]: r["n"] for r in rows}
