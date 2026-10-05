"""Reading and writing one fetch run's results (BRIEF.md steps 6 and 9).

Writes are grouped per run in transactions; nothing here deletes a row. Pruning (D-002) only clears
raw_payload and old page-snapshot text after 30 days.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from ..dedupe import Cluster, Member, story_title
from ..fetchers.outcome import FetchOutcome, PageSnapshot, Validators
from ..normalise import Item
from ..scoring import Score, StoryFacts, TopicFacts
from ..sources import Source
from ..tagging.keywords import TagResult
from .connection import from_iso, utc_iso

FAILURE_STATUSES = {"error"}
RETENTION = timedelta(days=30)


def fields_present(outcome: FetchOutcome) -> str:
    """Which item fields this source actually returned (BRIEF.md: fetch_runs.fields_present)."""
    checks = {
        "title": lambda e: e.title,
        "link": lambda e: e.link,
        "date": lambda e: e.published,
        "summary": lambda e: e.summary,
        "image": lambda e: e.image_url,
        "author": lambda e: e.author,
    }
    return ",".join(name for name, has in checks.items() if any(has(e) for e in outcome.entries))


def record_fetch(conn: sqlite3.Connection, outcome: FetchOutcome, started_at: datetime) -> None:
    """One fetch_runs row, plus the source's health columns and conditional-GET validators."""
    dated = [e.published for e in outcome.entries if e.published]
    newest = utc_iso(max(dated)) if dated else None
    present = fields_present(outcome) if outcome.entries else None
    stamp = utc_iso(started_at)
    with conn:
        conn.execute(
            "INSERT INTO fetch_runs (source_id, started_at, status, http_status, duration_ms, items_returned,"
            " newest_item_at, fields_present, on_backup, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                outcome.source_id,
                stamp,
                outcome.status,
                outcome.http_status,
                int(outcome.elapsed * 1000),
                len(outcome.entries),
                newest,
                present,
                int(outcome.on_backup),
                outcome.reason or None,
            ),
        )
        if outcome.status == "skipped":
            conn.execute(
                "UPDATE sources SET last_status = ? WHERE source_id = ?",
                ("skipped: " + outcome.reason, outcome.source_id),
            )
        elif outcome.status in FAILURE_STATUSES:
            conn.execute(
                "UPDATE sources SET last_status = ?, consecutive_failures = consecutive_failures + 1"
                " WHERE source_id = ?",
                (f"error: {outcome.reason}"[:300], outcome.source_id),
            )
        else:  # ok or not_modified
            conn.execute(
                "UPDATE sources SET last_status = ?, last_ok_at = ?, consecutive_failures = 0,"
                " newest_item_at = MAX(COALESCE(?, ''), COALESCE(newest_item_at, '')), fields_present = COALESCE(?, fields_present),"
                " etag = ?, last_modified = ? WHERE source_id = ?",
                (
                    outcome.status,
                    stamp,
                    newest,
                    present,
                    outcome.validators.etag or None,
                    outcome.validators.last_modified or None,
                    outcome.source_id,
                ),
            )
        if outcome.snapshot is not None and outcome.status == "ok":
            conn.execute(
                "INSERT OR IGNORE INTO page_snapshots (source_id, taken_at, text_hash, title, text)"
                " VALUES (?, ?, ?, ?, ?)",
                (outcome.source_id, stamp, outcome.snapshot.text_hash, outcome.snapshot.title, outcome.snapshot.text),
            )


def load_validators(conn: sqlite3.Connection) -> dict[str, Validators]:
    rows = conn.execute(
        "SELECT source_id, etag, last_modified FROM sources WHERE etag IS NOT NULL OR last_modified IS NOT NULL"
    )
    return {r["source_id"]: Validators(r["etag"] or "", r["last_modified"] or "") for r in rows}


def load_snapshots(conn: sqlite3.Connection) -> dict[str, PageSnapshot]:
    """Each monitored page's latest snapshot."""
    rows = conn.execute(
        "SELECT p.source_id, p.text_hash, p.title, p.text FROM page_snapshots p"
        " JOIN (SELECT source_id, MAX(taken_at) AS t FROM page_snapshots GROUP BY source_id) latest"
        " ON latest.source_id = p.source_id AND latest.t = p.taken_at"
    )
    return {r["source_id"]: PageSnapshot(r["text_hash"], r["text"] or "", r["title"] or "") for r in rows}


def load_gnews_cache(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["article_id"]: r["url"] for r in conn.execute("SELECT article_id, url FROM gnews_cache")}


def save_gnews_cache(conn: sqlite3.Connection, cache: dict[str, str], now: datetime) -> int:
    stamp = utc_iso(now)
    with conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO gnews_cache (article_id, url, resolved_at) VALUES (?, ?, ?)",
            [(aid, url, stamp) for aid, url in cache.items()],
        )
        return conn.total_changes - before


def load_recent_clusters(conn: sqlite3.Connection, now: datetime, window: timedelta) -> list[Cluster]:
    """Stories that started within `window` before `now`, with their members, for the next run's matching."""
    since = utc_iso(now - window)
    clusters: dict[str, Cluster] = {}
    for r in conn.execute(
        "SELECT cluster_id, headline, first_seen_at, page_monitor FROM story_clusters WHERE started_at >= ?", (since,)
    ):
        clusters[r["cluster_id"]] = Cluster(
            r["cluster_id"], r["headline"], from_iso(r["first_seen_at"]), page_monitor=bool(r["page_monitor"])
        )
    if not clusters:
        return []
    for r in conn.execute(
        "SELECT i.item_id, i.source_id, i.publisher, i.title, i.published_at, i.cluster_id FROM items i"
        " JOIN story_clusters c ON c.cluster_id = i.cluster_id WHERE c.started_at >= ?",
        (since,),
    ):
        clusters[r["cluster_id"]].members.append(
            Member(
                r["item_id"],
                r["source_id"],
                r["publisher"] or "",
                r["title"],
                story_title(r["title"], r["publisher"] or ""),
                from_iso(r["published_at"]),
            )
        )
    return [c for c in clusters.values() if c.members]


def load_sources(conn: sqlite3.Connection, source_ids: list[str] | None = None) -> list[Source]:
    """Active sources, in source_id order; only `source_ids` when given (inactive ones included then,
    so a person can still test a source by hand)."""
    if source_ids:
        rows = conn.execute(
            "SELECT * FROM sources WHERE source_id IN (SELECT value FROM json_each(?)) ORDER BY source_id",
            (json.dumps(source_ids),),
        )
    else:
        rows = conn.execute("SELECT * FROM sources WHERE active = 1 ORDER BY source_id")
    return [
        Source(
            source_id=r["source_id"],
            name=r["name"],
            category=r["category"] or "",
            route_type=r["route_type"],
            fetch_url=r["fetch_url"] or "",
            link_or_handle=r["link_or_handle"] or "",
            topics_hint=r["topics_hint"] or "",
            tier=r["tier"] or "",
            priority_x_feed=bool(r["priority_x_feed"]),
            backup_google_news_url=r["backup_google_news_url"] or "",
            known_status=r["known_status"] or "",
            notes=r["notes"] or "",
        )
        for r in rows
    ]


def known_item_ids(conn: sqlite3.Connection, item_ids: list[str]) -> set[str]:
    """Which of `item_ids` are already stored."""
    if not item_ids:
        return set()
    rows = conn.execute(
        "SELECT item_id FROM items WHERE item_id IN (SELECT value FROM json_each(?))", (json.dumps(item_ids),)
    )
    return {r[0] for r in rows}


def save_run(conn: sqlite3.Connection, items: list[Item], clusters: list[Cluster], now: datetime) -> int:
    """Store new items and the stories they belong to (new or grown). Returns how many items were new.
    One transaction: a crash leaves the database as it was before the run.

    Safe against items seen before: an item already stored keeps its story, a story is only written when
    it gets at least one new item, and every story's counts are recomputed from its stored items - so an
    old article that a feed lists again can never shrink or duplicate a story."""
    stamp = utc_iso(now)
    known = known_item_ids(conn, [i.item_id for i in items])
    new_items = list({i.item_id: i for i in reversed(items) if i.item_id not in known}.values())[
        ::-1
    ]  # first copy wins
    new_ids = {i.item_id for i in new_items}
    cluster_of = {m.item_id: c.cluster_id for c in clusters for m in c.members}
    growing = [c for c in clusters if any(m.item_id in new_ids for m in c.members)]
    with conn:
        for c in growing:
            conn.execute(
                "INSERT INTO story_clusters (cluster_id, headline, first_seen_at, started_at, page_monitor,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(cluster_id) DO UPDATE SET"
                " started_at = MIN(started_at, excluded.started_at), updated_at = excluded.updated_at",
                (c.cluster_id, c.headline, utc_iso(c.first_seen_at), utc_iso(c.started_at), int(c.page_monitor), stamp),
            )
        conn.executemany(
            "INSERT INTO items (item_id, source_id, title, url, canonical_url, publisher, published_at,"
            " fetched_at, summary, image_url, author, language, raw_guid, raw_payload, url_resolved, on_backup,"
            " cluster_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    i.item_id,
                    i.source_id,
                    i.title or "",  # NOT NULL columns get a value, so no row can be dropped silently
                    i.url,
                    i.canonical_url or i.url,
                    i.publisher,
                    utc_iso(i.published_at or i.fetched_at),
                    utc_iso(i.fetched_at),
                    i.summary,
                    i.image_url,
                    i.author,
                    i.language,
                    i.raw_guid,
                    json.dumps(i.raw_payload, ensure_ascii=False, default=str),
                    int(i.url_resolved),
                    int(i.on_backup),
                    cluster_of.get(i.item_id),
                )
                for i in new_items
            ],
        )
        for c in growing:
            conn.execute(
                "UPDATE story_clusters SET"
                " source_count = (SELECT COUNT(DISTINCT source_id) FROM items WHERE cluster_id = ?1),"
                " publisher_count = (SELECT COUNT(DISTINCT LOWER(publisher)) FROM items"
                "   WHERE cluster_id = ?1 AND publisher IS NOT NULL AND publisher <> ''),"
                " sources = (SELECT json_group_array(source_id) FROM"
                "   (SELECT DISTINCT source_id FROM items WHERE cluster_id = ?1 ORDER BY source_id))"
                " WHERE cluster_id = ?1",
                (c.cluster_id,),
            )
    return len(new_items)


def save_tags(conn: sqlite3.Connection, tags: dict[str, TagResult], now: datetime) -> None:
    """Replace each story's keyword tags (recomputed from the whole story each run) and its
    unmatched-local flag. AI tags (match_method llm) are never touched here."""
    stamp = utc_iso(now)
    with conn:
        for cluster_id, result in tags.items():
            conn.execute(
                "UPDATE story_clusters SET unmatched_local = ? WHERE cluster_id = ?",
                (int(result.unmatched_local), cluster_id),
            )
            conn.execute("DELETE FROM item_topics WHERE cluster_id = ? AND match_method = 'keyword'", (cluster_id,))
            conn.executemany(
                "INSERT INTO item_topics (cluster_id, topic_id, match_method, confidence, evidence, tagged_at)"
                " VALUES (?, ?, 'keyword', NULL, ?, ?)",
                [(cluster_id, tid, json.dumps(hits, ensure_ascii=False), stamp) for tid, hits in result.topics.items()],
            )


def story_texts(conn: sqlite3.Connection, cluster_id: str) -> list[str]:
    """Every member's title and summary, for tagging the whole story."""
    return [
        f"{r['title']}\n{r['summary'] or ''}"
        for r in conn.execute("SELECT title, summary FROM items WHERE cluster_id = ?", (cluster_id,))
    ]


def prune(conn: sqlite3.Connection, now: datetime) -> tuple[int, int]:
    """D-002: clear raw_payload and old page-snapshot text after 30 days (keeping each page's latest
    snapshot, which the next comparison needs). Rows and story summaries are kept forever."""
    cutoff = utc_iso(now - RETENTION)
    with conn:
        payloads = conn.execute(
            "UPDATE items SET raw_payload = NULL WHERE fetched_at < ? AND raw_payload IS NOT NULL", (cutoff,)
        ).rowcount
        texts = conn.execute(
            "UPDATE page_snapshots SET text = NULL WHERE taken_at < ? AND text IS NOT NULL AND taken_at < ("
            " SELECT MAX(taken_at) FROM page_snapshots p2 WHERE p2.source_id = page_snapshots.source_id)",
            (cutoff,),
        ).rowcount
    return payloads, texts


def story_facts(conn: sqlite3.Connection, cluster_id: str) -> StoryFacts:
    """What a story is scored on. Topics: the AI's when it has tagged the story, else the keyword pass's."""
    tagged = conn.execute(
        "SELECT it.topic_id, it.match_method, t.priority, t.geography, t.sensitive_note FROM item_topics it"
        " LEFT JOIN topics t ON t.topic_id = it.topic_id WHERE it.cluster_id = ?",
        (cluster_id,),
    ).fetchall()
    methods = {r["match_method"] for r in tagged}
    use = "llm" if "llm" in methods else "keyword"
    topics = [
        TopicFacts(r["topic_id"], r["priority"] or "", r["geography"] or "", bool((r["sensitive_note"] or "").strip()))
        for r in tagged
        if r["match_method"] == use
    ]
    tiers = [
        r["tier"] or ""
        for r in conn.execute(
            "SELECT DISTINCT s.source_id, s.tier FROM items i JOIN sources s ON s.source_id = i.source_id"
            " WHERE i.cluster_id = ?",
            (cluster_id,),
        )
    ]
    return StoryFacts(topics=topics, source_tiers=tiers)


def save_score(conn: sqlite3.Connection, cluster_id: str, score: Score) -> None:
    with conn:
        conn.execute(
            "UPDATE story_clusters SET relevance_score = ?, label = ?, sensitive = ? WHERE cluster_id = ?",
            (score.total, score.label, int(score.sensitive), cluster_id),
        )


def top_stories(conn: sqlite3.Connection, since: datetime, limit: int, label: str | None = None) -> list[sqlite3.Row]:
    """Stories last updated since `since`, best first, with their topic ids (AI's if any, else keyword)."""
    return conn.execute(
        "SELECT c.cluster_id, c.headline, c.relevance_score, c.label, c.sensitive, c.source_count, c.started_at,"
        " (SELECT group_concat(topic_id, ' ') FROM (SELECT DISTINCT topic_id FROM item_topics it"
        "   WHERE it.cluster_id = c.cluster_id ORDER BY topic_id)) AS topics,"
        " EXISTS (SELECT 1 FROM item_topics it WHERE it.cluster_id = c.cluster_id AND it.match_method = 'llm')"
        "   AS ai_checked"
        " FROM story_clusters c WHERE c.updated_at >= ? AND (? IS NULL OR c.label = ?)"
        " ORDER BY c.relevance_score DESC, c.source_count DESC, c.started_at DESC LIMIT ?",
        (utc_iso(since), label, label, limit),
    ).fetchall()


# ---- the run diary (step 7, D-008)

DIARY_MODES = {"due", "all", "source"}  # the timer's `fetch --due`, or a person's `fetch --all` / `--source`
FINISHED_OUTCOMES = {"ok", "offline", "failed"}  # how a started run ends
TICK_OUTCOMES = {"nothing_due", "busy", "failed"}  # one-shot rows: fetched nothing, or failed before a run could start


def _check(value: str, allowed: set[str], what: str) -> None:
    # free text in the table, so a typo here would quietly mislead `status` and the day-14 report
    if value not in allowed:
        raise ValueError(f"unknown run {what}: {value!r} (expected one of {sorted(allowed)})")


def last_attempted(conn: sqlite3.Connection) -> dict[str, datetime]:
    """When each source was last tried, whatever the result: the scheduler counts intervals from here. One index
    lookup per source, so it stays fast however long fetch_runs grows."""
    rows = conn.execute(
        "SELECT s.source_id, (SELECT MAX(f.started_at) FROM fetch_runs f WHERE f.source_id = s.source_id) AS last"
        " FROM sources s"
    )
    return {r["source_id"]: from_iso(r["last"]) for r in rows if r["last"]}


def start_engine_run(conn: sqlite3.Connection, started_at: datetime, mode: str, sources_due: int) -> int:
    """A diary row marked 'running'; finish_engine_run closes it. A row left 'running' is a run that never
    finished (killed or crashed)."""
    _check(mode, DIARY_MODES, "mode")
    with conn:
        cur = conn.execute(
            "INSERT INTO engine_runs (started_at, mode, outcome, sources_due) VALUES (?, ?, 'running', ?)",
            (utc_iso(started_at), mode, sources_due),
        )
    return cur.lastrowid


def finish_engine_run(
    conn: sqlite3.Connection,
    engine_run_id: int,
    finished_at: datetime,
    outcome: str,
    *,
    sources_ok: int | None = None,
    sources_failed: int | None = None,
    sources_skipped: int | None = None,
    items_new: int | None = None,
    google_refusals: int | None = None,
    error: str | None = None,
) -> None:
    """Close a 'running' row, once. Anything else (an unknown id, a finished run, a tick) is a bug and raises,
    so the diary never says something that did not happen."""
    _check(outcome, FINISHED_OUTCOMES, "outcome")
    with conn:
        updated = conn.execute(
            "UPDATE engine_runs SET finished_at = ?, outcome = ?, sources_ok = ?, sources_failed = ?,"
            " sources_skipped = ?, items_new = ?, google_refusals = ?, error = ?"
            " WHERE engine_run_id = ? AND outcome = 'running'",
            (
                utc_iso(finished_at),
                outcome,
                sources_ok,
                sources_failed,
                sources_skipped,
                items_new,
                google_refusals,
                error,
                engine_run_id,
            ),
        ).rowcount
    if updated != 1:
        raise ValueError(f"no running diary row with id {engine_run_id}")


def log_engine_tick(
    conn: sqlite3.Connection, at: datetime, mode: str, outcome: str, sources_due: int = 0, error: str | None = None
) -> int:
    """A check that fetched nothing ('nothing_due', or 'busy' while another run held the lock), or that failed
    before a run could start ('failed', with the error): one finished row, so the diary shows the engine was
    awake at that time."""
    _check(mode, DIARY_MODES, "mode")
    _check(outcome, TICK_OUTCOMES, "outcome")
    stamp = utc_iso(at)
    with conn:
        cur = conn.execute(
            "INSERT INTO engine_runs (started_at, finished_at, mode, outcome, sources_due, error)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (stamp, stamp, mode, outcome, sources_due, error),
        )
    return cur.lastrowid


def engine_runs_since(conn: sqlite3.Connection, since: datetime) -> list[sqlite3.Row]:
    """Diary rows started at or after `since`, oldest first."""
    return conn.execute(
        "SELECT * FROM engine_runs WHERE started_at >= ? ORDER BY started_at, engine_run_id", (utc_iso(since),)
    ).fetchall()


def last_engine_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The latest diary row that fetched, or tried to: checks with nothing due or a busy lock are skipped."""
    return conn.execute(
        "SELECT * FROM engine_runs WHERE outcome NOT IN ('nothing_due', 'busy')"
        " ORDER BY started_at DESC, engine_run_id DESC LIMIT 1"
    ).fetchone()


def first_engine_run_at(conn: sqlite3.Connection) -> datetime | None:
    """When the diary began, so `status` can tell an outage that started before its 24 h window."""
    return from_iso(conn.execute("SELECT MIN(started_at) FROM engine_runs").fetchone()[0])


def failing_sources(conn: sqlite3.Connection, at_least: int = 3) -> list[sqlite3.Row]:
    """Active sources that failed `at_least` runs in a row (BRIEF.md: flagged for replacement after 3)."""
    return conn.execute(
        "SELECT source_id, name, consecutive_failures, last_status FROM sources"
        " WHERE active = 1 AND consecutive_failures >= ? ORDER BY consecutive_failures DESC, source_id",
        (at_least,),
    ).fetchall()
