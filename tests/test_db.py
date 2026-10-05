import json
from datetime import datetime, timedelta, timezone

import pytest

from riffi_ingest.db import connect
from riffi_ingest.db.connection import from_iso, utc_iso
from riffi_ingest.db.importers import import_sources, import_topics
from riffi_ingest.db.store import (
    engine_runs_since,
    failing_sources,
    finish_engine_run,
    first_engine_run_at,
    last_attempted,
    last_engine_run,
    load_gnews_cache,
    load_recent_clusters,
    load_snapshots,
    load_validators,
    log_engine_tick,
    prune,
    record_fetch,
    save_gnews_cache,
    save_run,
    save_tags,
    start_engine_run,
    story_texts,
)
from riffi_ingest.dedupe import Clusterer
from riffi_ingest.fetchers.outcome import FetchOutcome, PageSnapshot, Validators
from riffi_ingest.fetchers.parse import Entry
from riffi_ingest.normalise import Item, item_id
from riffi_ingest.tagging.keywords import TagResult

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, repo_root):
    conn = connect(tmp_path / "data" / "engine.db")
    import_sources(conn, repo_root / "feeds.csv", now=NOW)
    import_topics(conn, repo_root / "topics.csv", now=NOW)
    yield conn
    conn.close()


def item(title, source_id="S011", hours_ago=1, url=None):
    url = url or f"https://a.in/{abs(hash((title, source_id)))}"
    return Item(
        item_id=item_id(url),
        source_id=source_id,
        title=title,
        url=url,
        canonical_url=url,
        publisher="Deccan Herald",
        published_at=NOW - timedelta(hours=hours_ago),
        fetched_at=NOW,
        summary="summary of " + title,
        raw_payload={"title": title},
    )


def test_connect_creates_the_file_with_wal_and_is_reopenable(tmp_path):
    path = tmp_path / "data" / "engine.db"
    connect(path).close()
    conn = connect(path)
    assert path.exists()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {
        "sources",
        "fetch_runs",
        "items",
        "story_clusters",
        "item_topics",
        "page_snapshots",
        "ground_truth",
    } <= tables


def test_a_newer_schema_is_refused(tmp_path):
    path = tmp_path / "engine.db"
    conn = connect(path)
    conn.execute("UPDATE schema_version SET version = 99")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError, match="newer engine"):
        connect(path)


def test_imports_are_idempotent_and_keep_history(db, repo_root, tmp_path):
    assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 131
    assert db.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 151
    db.execute("UPDATE sources SET last_ok_at = '2026-10-04T05:00:00+00:00', consecutive_failures = 2")
    db.commit()
    again = import_sources(db, repo_root / "feeds.csv", now=NOW)
    assert (again.added, again.updated, again.deactivated) == (0, 131, 0)
    row = db.execute("SELECT last_ok_at, consecutive_failures FROM sources WHERE source_id = 'S011'").fetchone()
    assert tuple(row) == ("2026-10-04T05:00:00+00:00", 2)  # health columns survive a re-import
    assert import_topics(db, repo_root / "topics.csv", now=NOW).updated == 151

    smaller = tmp_path / "feeds.csv"
    lines = (repo_root / "feeds.csv").read_text(encoding="utf-8").splitlines()
    smaller.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # drop the last source
    result = import_sources(db, smaller, now=NOW)
    assert result.deactivated == 1
    assert db.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 131  # never deleted
    assert db.execute("SELECT COUNT(*) FROM sources WHERE active = 0").fetchone()[0] == 1


def test_record_fetch_tracks_health_and_validators(db):
    ok = FetchOutcome(
        "S011",
        "Native publisher RSS/Atom",
        "ok",
        http_status=200,
        entries=[Entry(title="a", link="https://a.in/1", published=NOW - timedelta(hours=1), summary="s")],
        validators=Validators('"e1"', ""),
        elapsed=1.25,
    )
    record_fetch(db, ok, NOW)
    s = db.execute("SELECT * FROM sources WHERE source_id = 'S011'").fetchone()
    assert s["last_status"] == "ok" and s["last_ok_at"] == utc_iso(NOW) and s["etag"] == '"e1"'
    assert s["fields_present"] == "title,link,date,summary" and s["newest_item_at"] == utc_iso(NOW - timedelta(hours=1))
    run = db.execute("SELECT * FROM fetch_runs").fetchone()
    assert (run["status"], run["http_status"], run["duration_ms"], run["items_returned"]) == ("ok", 200, 1250, 1)
    assert load_validators(db)["S011"].etag == '"e1"'

    for _ in range(3):
        record_fetch(db, FetchOutcome("S011", "Native publisher RSS/Atom", "error", reason="HTTP 503"), NOW)
    s = db.execute("SELECT * FROM sources WHERE source_id = 'S011'").fetchone()
    assert s["consecutive_failures"] == 3 and s["last_status"] == "error: HTTP 503"
    assert s["last_ok_at"] == utc_iso(NOW) and s["etag"] == '"e1"'  # a failure keeps the last good state
    record_fetch(
        db, FetchOutcome("S011", "Native publisher RSS/Atom", "not_modified", validators=Validators('"e1"')), NOW
    )
    assert db.execute("SELECT consecutive_failures FROM sources WHERE source_id = 'S011'").fetchone()[0] == 0


def test_page_snapshots_round_trip_latest_only(db):
    for i, text in enumerate(["v1", "v2"]):
        out = FetchOutcome("S025", "Web page monitor", "ok", snapshot=PageSnapshot(f"h{i}", text, "BMRCL"))
        record_fetch(db, out, NOW + timedelta(hours=i))
    assert load_snapshots(db) == {"S025": PageSnapshot("h1", "v2", "BMRCL")}


def test_items_clusters_and_tags_round_trip(db):
    c = Clusterer()
    first = [item("BBMP floats tender for tunnel road", hours_ago=3), item("Metro fare hike from Monday", hours_ago=2)]
    c.add_all(first)
    assert save_run(db, first, list(c.clusters.values()), NOW) == 2
    assert save_run(db, first, list(c.clusters.values()), NOW) == 0  # same items again: nothing new

    # next run: stories come back from the database and a new report joins one of them
    later = NOW + timedelta(minutes=30)
    existing = load_recent_clusters(db, later, timedelta(hours=48))
    assert len(existing) == 2
    c2 = Clusterer(existing=existing)
    new = item("Tunnel road tender floated by BBMP", source_id="S019", hours_ago=1)
    joined = c2.add(new).cluster
    save_run(db, [new], [joined], later)
    row = db.execute(
        "SELECT source_count, sources FROM story_clusters WHERE cluster_id = ?", (joined.cluster_id,)
    ).fetchone()
    assert row["source_count"] == 2 and json.loads(row["sources"]) == ["S011", "S019"]
    assert len(story_texts(db, joined.cluster_id)) == 2

    save_tags(db, {joined.cluster_id: TagResult({"O06": ["tunnel road"]}, local=True)}, later)
    save_tags(db, {joined.cluster_id: TagResult({"O06": ["tunnel road", "bbmp"]}, local=True)}, later)  # replaced
    db.execute(
        "INSERT INTO item_topics VALUES (?, 'O06', 'llm', 0.9, '\"confirmed\"', ?)", (joined.cluster_id, utc_iso(later))
    )
    db.commit()
    save_tags(db, {joined.cluster_id: TagResult({}, local=True)}, later)
    rows = db.execute("SELECT match_method FROM item_topics WHERE cluster_id = ?", (joined.cluster_id,)).fetchall()
    assert [r[0] for r in rows] == ["llm"]  # keyword tags recomputed, AI tags untouched
    assert db.execute(
        "SELECT unmatched_local FROM story_clusters WHERE cluster_id = ?", (joined.cluster_id,)
    ).fetchone()[0]


def test_old_stories_are_not_loaded(db):
    c = Clusterer()
    old = [item("Cauvery water released to Tamil Nadu", hours_ago=60)]
    c.add_all(old)
    save_run(db, old, list(c.clusters.values()), NOW)
    assert load_recent_clusters(db, NOW, timedelta(hours=48)) == []


def test_gnews_cache_round_trip(db):
    assert save_gnews_cache(db, {"CBMi1": "https://a.in/1"}, NOW) == 1
    assert save_gnews_cache(db, {"CBMi1": "https://a.in/1", "CBMi2": "https://a.in/2"}, NOW) == 1
    assert load_gnews_cache(db) == {"CBMi1": "https://a.in/1", "CBMi2": "https://a.in/2"}


def test_prune_clears_old_payloads_and_old_snapshot_text_only(db):
    c = Clusterer()
    old, new = item("Old story", hours_ago=24 * 40), item("New story")
    old.fetched_at = NOW - timedelta(days=40)
    c.add_all([old, new])
    save_run(db, [old, new], list(c.clusters.values()), NOW)
    record_fetch(
        db,
        FetchOutcome("S025", "Web page monitor", "ok", snapshot=PageSnapshot("h0", "old text")),
        NOW - timedelta(days=40),
    )
    record_fetch(
        db,
        FetchOutcome("S025", "Web page monitor", "ok", snapshot=PageSnapshot("h1", "kept")),
        NOW - timedelta(days=35),
    )
    assert prune(db, NOW) == (1, 1)
    payloads = dict(db.execute("SELECT title, raw_payload FROM items").fetchall())
    assert payloads["Old story"] is None and payloads["New story"]
    assert load_snapshots(db)["S025"].text == "kept"  # the latest snapshot keeps its text, however old


def test_dates_round_trip_as_utc():
    ist = timezone(timedelta(hours=5, minutes=30))
    when = datetime(2026, 10, 4, 11, 30, tzinfo=ist)
    assert utc_iso(when) == "2026-10-04T06:00:00+00:00"
    assert from_iso(utc_iso(when)) == when
    assert utc_iso(datetime(2026, 10, 4, 6, 0)) == "2026-10-04T06:00:00+00:00"  # naive = UTC
    assert from_iso(None) is None


# ---- found by the adversarial review, 4 Oct 2026


def test_an_old_article_listed_again_never_shrinks_or_duplicates_its_story(db):
    a = item("Karnataka cabinet approves caste survey report", source_id="S011", hours_ago=2)
    b = item("Cabinet approves caste survey report in Karnataka", source_id="S019", hours_ago=1)
    c = Clusterer()
    c.add_all([a, b])
    save_run(db, [a, b], list(c.clusters.values()), NOW)
    story_id = next(iter(c.clusters))
    # 54 h later the story is outside the 48 h window; the S011 feed still lists the same article
    later = NOW + timedelta(hours=54)
    c2 = Clusterer(existing=load_recent_clusters(db, later, timedelta(hours=48)))
    c2.add(a)
    assert save_run(db, [a], list(c2.clusters.values()), later) == 0
    row = db.execute("SELECT source_count, sources FROM story_clusters WHERE cluster_id = ?", (story_id,)).fetchone()
    assert row["source_count"] == 2 and json.loads(row["sources"]) == ["S011", "S019"]
    # and a later member coming back alone makes no orphan story
    c3 = Clusterer()
    c3.add(b)
    save_run(db, [b], list(c3.clusters.values()), later)
    orphans = db.execute(
        "SELECT COUNT(*) FROM story_clusters c WHERE NOT EXISTS (SELECT 1 FROM items i WHERE i.cluster_id = c.cluster_id)"
    ).fetchone()[0]
    assert orphans == 0


def test_items_with_missing_fields_are_stored_not_dropped(db):
    odd = item("x")
    odd.title = None
    odd.published_at = None
    c = Clusterer()
    c.add(odd)
    assert save_run(db, [odd], list(c.clusters.values()), NOW) == 1
    row = db.execute("SELECT title, published_at FROM items WHERE item_id = ?", (odd.item_id,)).fetchone()
    assert row["title"] == "" and row["published_at"] == utc_iso(NOW)


def test_the_same_article_twice_in_one_run_is_stored_once(db):
    a = item("Metro fare hike from Monday", url="https://a.in/fare")
    twin = item("Metro fare hike from Monday", source_id="S019", url="https://a.in/fare")
    c = Clusterer()
    c.add_all([a, twin])
    assert save_run(db, [a, twin], list(c.clusters.values()), NOW) == 1


def test_newest_item_date_never_goes_backwards(db):
    fresh = FetchOutcome(
        "S019", "Google News RSS", "ok", entries=[Entry(title="a", link="https://a.in/1", published=NOW)]
    )
    older = FetchOutcome(
        "S019",
        "Google News RSS",
        "ok",
        entries=[Entry(title="b", link="https://a.in/2", published=NOW - timedelta(days=3))],
    )
    record_fetch(db, fresh, NOW)
    record_fetch(db, older, NOW + timedelta(hours=1))
    assert db.execute("SELECT newest_item_at FROM sources WHERE source_id = 'S019'").fetchone()[0] == utc_iso(NOW)


def test_a_database_inside_onedrive_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="OneDrive"):
        connect(tmp_path / "OneDrive - Riffi" / "engine.db")


def test_the_default_database_lives_in_the_project_not_the_current_folder(monkeypatch, tmp_path, repo_root):
    from riffi_ingest.db.connection import default_db_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RIFFI_DB_PATH", raising=False)
    assert default_db_path() == repo_root / "data" / "engine.db"
    monkeypatch.setenv("RIFFI_DB_PATH", str(tmp_path / "x.db"))
    assert default_db_path() == tmp_path / "x.db"


def test_schema_version_has_exactly_one_row(tmp_path):
    path = tmp_path / "engine.db"
    for _ in range(3):
        connect(path).close()
    assert connect(path).execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


# ---- the run diary (engine_runs), 5 Oct 2026: evidence for every 30 minutes of the two-week test

HOUR = timedelta(hours=1)
HALF_HOUR = timedelta(minutes=30)
NATIVE = "Native publisher RSS/Atom"
DIARY_COUNTS = ("sources_ok", "sources_failed", "sources_skipped", "items_new", "google_refusals")


def fail(conn, source_id, times, route_type=NATIVE, at=NOW):
    for _ in range(times):
        record_fetch(conn, FetchOutcome(source_id, route_type, "error", reason="HTTP 503"), at)


def run_row(conn, engine_run_id):
    return conn.execute("SELECT * FROM engine_runs WHERE engine_run_id = ?", (engine_run_id,)).fetchone()


def other_tables(conn):
    """Every row of every table except the diary (and SQLite's own counter table), for before/after checks."""
    names = [
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        if r[0] not in ("engine_runs", "sqlite_sequence")
    ]
    return {n: [tuple(r) for r in conn.execute(f'SELECT * FROM "{n}" ORDER BY rowid')] for n in names}


def test_the_diary_table_has_the_agreed_columns_and_an_index_on_start_time(tmp_path):
    conn = connect(tmp_path / "engine.db")
    info = {r["name"]: r for r in conn.execute("PRAGMA table_info(engine_runs)")}
    assert set(info) == {
        "engine_run_id",
        "started_at",
        "finished_at",
        "mode",
        "outcome",
        "sources_due",
        "sources_ok",
        "sources_failed",
        "sources_skipped",
        "items_new",
        "google_refusals",
        "error",
    }
    assert info["engine_run_id"]["pk"] == 1 and info["engine_run_id"]["type"] == "INTEGER"
    required = {"started_at", "mode", "outcome", "sources_due"}  # NOT NULL; everything else may be empty
    assert {name for name, col in info.items() if col["notnull"] and name != "engine_run_id"} == required
    assert info["sources_due"]["dflt_value"] == "0"
    assert all(info[c]["type"] == "TEXT" for c in ("started_at", "finished_at", "mode", "outcome", "error"))
    assert all(info[c]["type"] == "INTEGER" for c in ("sources_due", *DIARY_COUNTS))
    indexed = [
        conn.execute(f'PRAGMA index_info("{ix["name"]}")').fetchall()[0]["name"]
        for ix in conn.execute("PRAGMA index_list(engine_runs)")
        if ix["origin"] == "c"
    ]
    assert "started_at" in indexed  # the diary is read by time


def test_diary_ids_are_never_reused(db):
    first = start_engine_run(db, NOW, "due", 1)
    db.execute("DELETE FROM engine_runs WHERE engine_run_id = ?", (first,))
    db.commit()
    assert start_engine_run(db, NOW + HOUR, "due", 1) > first  # AUTOINCREMENT, not rowid reuse


# -- last_attempted


def test_last_attempted_is_the_latest_fetch_whatever_its_status(db):
    assert last_attempted(db) == {}  # nothing fetched yet
    # S011: the latest start wins even when an older fetch is written afterwards
    record_fetch(db, FetchOutcome("S011", NATIVE, "error", reason="HTTP 503"), NOW + 3 * HOUR)
    record_fetch(db, FetchOutcome("S011", NATIVE, "ok"), NOW + HOUR)
    record_fetch(db, FetchOutcome("S034", NATIVE, "skipped", reason="over the cap"), NOW)
    record_fetch(db, FetchOutcome("S112", "Manual", "not_modified"), NOW + 2 * HOUR)
    result = last_attempted(db)
    assert isinstance(result, dict)
    assert result == {"S011": NOW + 3 * HOUR, "S034": NOW, "S112": NOW + 2 * HOUR}
    assert "S019" not in result and "S025" not in result  # never fetched: absent, not "epoch" or None
    assert all(when.tzinfo is not None for when in result.values())


def test_last_attempted_counts_a_source_whose_only_fetches_failed(db):
    fail(db, "S011", 2, at=NOW - HOUR)
    assert last_attempted(db) == {"S011": NOW - HOUR}


# -- the diary row lifecycle


def test_a_diary_row_starts_running_and_is_finished_with_its_counts(db):
    run_id = start_engine_run(db, NOW, "due", 12)
    assert isinstance(run_id, int)
    row = run_row(db, run_id)
    assert row["started_at"] == utc_iso(NOW)
    assert row["finished_at"] is None
    assert (row["mode"], row["outcome"], row["sources_due"]) == ("due", "running", 12)
    assert row["error"] is None and all(row[c] is None for c in DIARY_COUNTS)

    finish_engine_run(
        db,
        run_id,
        NOW + timedelta(minutes=7),
        "ok",
        sources_ok=10,
        sources_failed=1,
        sources_skipped=1,
        items_new=42,
        google_refusals=3,
    )
    row = run_row(db, run_id)
    assert from_iso(row["finished_at"]) == NOW + timedelta(minutes=7)
    assert row["outcome"] == "ok"
    assert (row["sources_ok"], row["sources_failed"], row["sources_skipped"]) == (10, 1, 1)
    assert (row["items_new"], row["google_refusals"]) == (42, 3)
    assert row["error"] is None
    # the start facts are not rewritten
    assert (row["started_at"], row["mode"], row["sources_due"]) == (utc_iso(NOW), "due", 12)
    assert db.execute("SELECT COUNT(*) FROM engine_runs").fetchone()[0] == 1


def test_finishing_a_run_touches_that_row_only_and_zero_counts_stay_zero(db):
    earlier = start_engine_run(db, NOW - HOUR, "all", 131)
    run_id = start_engine_run(db, NOW, "source", 1)
    finish_engine_run(
        db,
        run_id,
        NOW + timedelta(minutes=2),
        "failed",
        sources_ok=0,
        sources_failed=0,
        sources_skipped=0,
        items_new=0,
        google_refusals=0,
        error="database is locked",
    )
    row = run_row(db, run_id)
    assert row["outcome"] == "failed" and row["error"] == "database is locked"
    assert all(row[c] == 0 and row[c] is not None for c in DIARY_COUNTS)  # 0 is a count, not "unknown"
    left_alone = run_row(db, earlier)
    assert left_alone["outcome"] == "running" and left_alone["finished_at"] is None
    assert left_alone["error"] is None and all(left_alone[c] is None for c in DIARY_COUNTS)
    assert left_alone["mode"] == "all" and left_alone["sources_due"] == 131


def test_an_offline_run_records_why_and_leaves_unknown_counts_empty(db):
    run_id = start_engine_run(db, NOW, "due", 9)
    finish_engine_run(db, run_id, NOW + timedelta(seconds=20), "offline", error="no internet")
    row = run_row(db, run_id)
    assert (row["outcome"], row["error"]) == ("offline", "no internet")
    assert from_iso(row["finished_at"]) == NOW + timedelta(seconds=20)
    assert all(row[c] is None for c in DIARY_COUNTS)


def test_a_run_started_in_another_time_zone_is_stored_as_utc(db):
    ist = timezone(timedelta(hours=5, minutes=30))
    run_id = start_engine_run(db, datetime(2026, 10, 4, 11, 30, tzinfo=ist), "all", 3)
    assert run_row(db, run_id)["started_at"] == "2026-10-04T06:00:00+00:00"


# -- ticks: a timer check that fetched nothing


def test_a_tick_is_a_one_shot_row_that_starts_and_ends_at_the_same_moment(db):
    nothing = log_engine_tick(db, NOW, "due", "nothing_due")
    busy = log_engine_tick(db, NOW + HALF_HOUR, "due", "busy", sources_due=5)
    assert isinstance(nothing, int) and isinstance(busy, int) and busy > nothing
    row = run_row(db, nothing)
    assert row["started_at"] == row["finished_at"] == utc_iso(NOW)
    assert (row["mode"], row["outcome"], row["sources_due"]) == ("due", "nothing_due", 0)
    row = run_row(db, busy)
    assert row["started_at"] == row["finished_at"] == utc_iso(NOW + HALF_HOUR)
    assert (row["mode"], row["outcome"], row["sources_due"]) == ("due", "busy", 5)
    assert db.execute("SELECT COUNT(*) FROM engine_runs").fetchone()[0] == 2


# -- engine_runs_since


def test_engine_runs_since_lists_oldest_first_from_the_cutoff_ties_by_id(db):
    too_old = log_engine_tick(db, NOW - HALF_HOUR, "due", "nothing_due")
    late = start_engine_run(db, NOW + 2 * HOUR, "due", 3)  # written first, started last
    on_cutoff = start_engine_run(db, NOW, "due", 1)
    middle = log_engine_tick(db, NOW + HOUR, "due", "busy")
    on_cutoff_too = log_engine_tick(db, NOW, "due", "nothing_due")  # same instant, higher id

    rows = engine_runs_since(db, NOW)
    assert isinstance(rows, list)
    assert [r["engine_run_id"] for r in rows] == [on_cutoff, on_cutoff_too, middle, late]
    assert [r["outcome"] for r in rows] == ["running", "nothing_due", "busy", "running"]  # ticks are included
    assert from_iso(rows[0]["started_at"]) == NOW and rows[0]["mode"] == "due" and rows[0]["sources_due"] == 1

    ist = timezone(timedelta(hours=5, minutes=30))
    assert [r["engine_run_id"] for r in engine_runs_since(db, NOW.astimezone(ist))] == [
        r["engine_run_id"] for r in rows
    ]  # the cutoff is an instant, whatever zone it is written in
    everything = engine_runs_since(db, NOW - timedelta(days=1))
    assert [r["engine_run_id"] for r in everything] == [too_old, on_cutoff, on_cutoff_too, middle, late]
    assert engine_runs_since(db, NOW + 3 * HOUR) == []


# -- last_engine_run


def test_last_engine_run_is_none_when_nothing_has_run(db):
    assert last_engine_run(db) is None


def test_last_engine_run_is_none_when_there_were_only_ticks(db):
    log_engine_tick(db, NOW, "due", "nothing_due")
    log_engine_tick(db, NOW + HALF_HOUR, "due", "busy", sources_due=2)
    assert last_engine_run(db) is None


@pytest.mark.parametrize("outcome", ["running", "ok", "offline", "failed"])
def test_last_engine_run_skips_newer_ticks_and_returns_the_real_run(db, outcome):
    run_id = start_engine_run(db, NOW, "due", 4)
    if outcome != "running":
        finish_engine_run(db, run_id, NOW + timedelta(minutes=3), outcome)
    log_engine_tick(db, NOW + HALF_HOUR, "due", "nothing_due")
    log_engine_tick(db, NOW + HOUR, "due", "busy", sources_due=1)
    row = last_engine_run(db)
    assert row is not None
    assert row["engine_run_id"] == run_id and row["outcome"] == outcome and row["mode"] == "due"


def test_last_engine_run_picks_the_latest_start_then_the_later_row(db):
    newest_start = start_engine_run(db, NOW, "due", 1)
    start_engine_run(db, NOW - HOUR, "all", 131)  # written later, but started earlier
    assert last_engine_run(db)["engine_run_id"] == newest_start
    same_instant = start_engine_run(db, NOW, "source", 1)
    assert last_engine_run(db)["engine_run_id"] == same_instant  # tie: the later row


# -- failing_sources


def test_failing_sources_lists_active_sources_at_the_threshold_most_failures_first(db):
    fail(db, "S112", 5, route_type="Manual")
    fail(db, "S034", 3)
    fail(db, "S011", 3)
    fail(db, "S019", 2, route_type="Google News RSS")  # below the default threshold
    fail(db, "S025", 6, route_type="Web page monitor")  # worst, but switched off
    db.execute("UPDATE sources SET active = 0 WHERE source_id = 'S025'")
    db.commit()
    fail(db, "S001", 3)  # three failures, then it recovered: the count is back to 0
    record_fetch(db, FetchOutcome("S001", NATIVE, "ok"), NOW)

    rows = failing_sources(db)
    assert [r["source_id"] for r in rows] == ["S112", "S011", "S034"]  # 5, then the 3s by source_id
    assert [r["consecutive_failures"] for r in rows] == [5, 3, 3]
    names = dict(db.execute("SELECT source_id, name FROM sources WHERE source_id IN ('S112', 'S011', 'S034')"))
    assert {r["source_id"]: r["name"] for r in rows} == names
    assert all(r["last_status"] == "error: HTTP 503" for r in rows)

    assert [r["source_id"] for r in failing_sources(db, at_least=2)] == ["S112", "S011", "S034", "S019"]
    assert [r["source_id"] for r in failing_sources(db, at_least=5)] == ["S112"]
    assert failing_sources(db, at_least=6) == []  # S025 has 6, but it is inactive


def test_failing_sources_is_empty_when_every_source_is_healthy(db):
    assert failing_sources(db) == []
    fail(db, "S011", 2)
    assert failing_sources(db) == []


# -- committed, and nothing else touched


def test_diary_rows_are_committed_and_visible_to_another_connection(tmp_path):
    path = tmp_path / "engine.db"
    writer, reader = connect(path), connect(path)

    run_id = start_engine_run(writer, NOW, "due", 4)
    assert not writer.in_transaction
    seen = reader.execute(
        "SELECT outcome, finished_at, sources_due FROM engine_runs WHERE engine_run_id = ?", (run_id,)
    )
    assert tuple(seen.fetchone()) == ("running", None, 4)

    finish_engine_run(writer, run_id, NOW + timedelta(minutes=5), "ok", sources_ok=4, items_new=9)
    assert not writer.in_transaction
    seen = reader.execute("SELECT outcome, sources_ok, items_new FROM engine_runs WHERE engine_run_id = ?", (run_id,))
    assert tuple(seen.fetchone()) == ("ok", 4, 9)

    tick_id = log_engine_tick(writer, NOW + HALF_HOUR, "due", "nothing_due")
    assert not writer.in_transaction
    seen = reader.execute("SELECT outcome FROM engine_runs WHERE engine_run_id = ?", (tick_id,)).fetchone()
    assert seen["outcome"] == "nothing_due"
    writer.close()
    reader.close()


def test_the_diary_functions_change_nothing_in_any_other_table(db):
    fail(db, "S011", 3)
    record_fetch(db, FetchOutcome("S034", NATIVE, "ok"), NOW)
    before = other_tables(db)
    run_id = start_engine_run(db, NOW, "due", 2)
    finish_engine_run(db, run_id, NOW + HOUR, "ok", sources_ok=2)
    log_engine_tick(db, NOW + HALF_HOUR, "due", "busy")
    engine_runs_since(db, NOW)
    last_engine_run(db)
    last_attempted(db)
    failing_sources(db)
    assert other_tables(db) == before


# -- an existing database gains the diary without losing anything


def test_an_existing_database_without_the_diary_gains_it_and_keeps_its_data(tmp_path, repo_root):
    path = tmp_path / "data" / "engine.db"
    conn = connect(path)
    import_sources(conn, repo_root / "feeds.csv", now=NOW)
    import_topics(conn, repo_root / "topics.csv", now=NOW)
    ok = FetchOutcome(
        "S011",
        NATIVE,
        "ok",
        http_status=200,
        entries=[Entry(title="a", link="https://a.in/1", published=NOW - HOUR, summary="s")],
        validators=Validators('"e1"', ""),
    )
    record_fetch(conn, ok, NOW)
    fail(conn, "S034", 2)
    record_fetch(
        conn, FetchOutcome("S025", "Web page monitor", "ok", snapshot=PageSnapshot("h0", "text", "BMRCL")), NOW
    )
    c = Clusterer()
    stories = [item("BBMP floats tender for tunnel road", hours_ago=3), item("Metro fare hike from Monday")]
    c.add_all(stories)
    save_run(conn, stories, list(c.clusters.values()), NOW)
    save_tags(conn, {cid: TagResult({"O06": ["tunnel road"]}, local=True) for cid in c.clusters}, NOW)
    save_gnews_cache(conn, {"CBMi1": "https://a.in/1"}, NOW)
    conn.execute(
        "INSERT INTO ground_truth (logged_on, topic_id, what_happened, created_at) VALUES ('2026-10-04', 'O06', 'x', ?)",
        (utc_iso(NOW),),
    )
    # make the file look like one written before this change: no diary table
    conn.execute("DROP TABLE engine_runs")
    conn.commit()
    assert "engine_runs" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    before = other_tables(conn)
    assert before["sources"] and before["fetch_runs"] and before["items"] and before["story_clusters"]
    assert before["item_topics"] and before["page_snapshots"] and before["gnews_cache"] and before["ground_truth"]
    conn.close()

    conn = connect(path)
    assert "engine_runs" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert other_tables(conn) == before  # every existing row of every other table is untouched
    assert tuple(conn.execute("SELECT id, version FROM schema_version").fetchall()[0]) == (1, 1)
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM engine_runs").fetchone()[0] == 0
    run_id = start_engine_run(conn, NOW, "due", 1)  # and the gained table works
    conn.close()

    conn = connect(path)  # connecting again neither fails nor empties the diary
    assert [r["engine_run_id"] for r in engine_runs_since(conn, NOW)] == [run_id]
    assert other_tables(conn) == before
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    conn.close()


# ---- the diary's stricter rules, added after the safety review (5 Oct 2026)

BAD_MODES = ["", "DUE", "due ", "hourly", "nothing_due", "running", None]


def diary(conn):
    """The whole diary, row by row, for before/after checks."""
    return [tuple(r) for r in conn.execute("SELECT * FROM engine_runs ORDER BY engine_run_id")]


# -- only the three agreed modes are accepted, and a rejected call writes nothing


@pytest.mark.parametrize("mode", ["due", "all", "source"])
def test_every_agreed_mode_is_accepted_by_a_run_and_by_a_tick(db, mode):
    run_id = start_engine_run(db, NOW, mode, 1)
    tick_id = log_engine_tick(db, NOW + HALF_HOUR, mode, "nothing_due")
    assert run_row(db, run_id)["mode"] == mode
    assert run_row(db, tick_id)["mode"] == mode


@pytest.mark.parametrize("mode", BAD_MODES)
def test_a_run_with_an_unknown_mode_is_refused_and_nothing_is_written(db, mode):
    assert diary(db) == []
    with pytest.raises(ValueError):
        start_engine_run(db, NOW, mode, 3)
    assert diary(db) == []
    assert not db.in_transaction  # a refused call leaves no half-open write behind

    kept = start_engine_run(db, NOW, "due", 3)  # and the diary still works afterwards
    before = diary(db)
    with pytest.raises(ValueError):
        start_engine_run(db, NOW + HOUR, mode, 3)
    assert diary(db) == before and run_row(db, kept)["outcome"] == "running"


@pytest.mark.parametrize("mode", BAD_MODES)
@pytest.mark.parametrize("outcome", ["nothing_due", "busy", "failed"])
def test_a_tick_with_an_unknown_mode_is_refused_and_nothing_is_written(db, mode, outcome):
    with pytest.raises(ValueError):
        log_engine_tick(db, NOW, mode, outcome)
    assert diary(db) == []
    assert not db.in_transaction


# -- a tick may only be nothing_due, busy or failed, and a refused tick writes nothing


@pytest.mark.parametrize("outcome", ["running", "ok", "offline", "", "NOTHING_DUE", "idle", None])
def test_a_tick_with_an_outcome_it_may_not_have_is_refused_and_nothing_is_written(db, outcome):
    with pytest.raises(ValueError):
        log_engine_tick(db, NOW, "due", outcome)
    assert diary(db) == []
    assert not db.in_transaction

    log_engine_tick(db, NOW, "due", "busy")
    before = diary(db)
    with pytest.raises(ValueError):
        log_engine_tick(db, NOW + HOUR, "due", outcome, sources_due=2, error="x")
    assert diary(db) == before


@pytest.mark.parametrize("outcome", ["nothing_due", "busy", "failed"])
def test_every_allowed_tick_outcome_is_stored_as_given(db, outcome):
    tick_id = log_engine_tick(db, NOW, "due", outcome, sources_due=2)
    row = run_row(db, tick_id)
    assert row["outcome"] == outcome and row["sources_due"] == 2
    assert row["started_at"] == row["finished_at"] == utc_iso(NOW)


# -- finish_engine_run takes only ok, offline or failed, and a refused finish changes nothing


@pytest.mark.parametrize("outcome", ["running", "nothing_due", "busy", "", "done", "OK", None])
def test_finishing_with_an_outcome_it_may_not_have_is_refused_and_the_row_stays_running(db, outcome):
    run_id = start_engine_run(db, NOW, "due", 4)
    other = start_engine_run(db, NOW + HOUR, "all", 9)
    before = diary(db)
    with pytest.raises(ValueError):
        finish_engine_run(db, run_id, NOW + timedelta(minutes=5), outcome, sources_ok=4, items_new=7, error="x")
    assert diary(db) == before
    row = run_row(db, run_id)
    assert row["outcome"] == "running" and row["finished_at"] is None and row["error"] is None
    assert all(row[c] is None for c in DIARY_COUNTS)

    finish_engine_run(db, run_id, NOW + timedelta(minutes=5), "ok", sources_ok=4)  # still closable, once
    assert run_row(db, run_id)["outcome"] == "ok" and run_row(db, other)["outcome"] == "running"


@pytest.mark.parametrize("outcome", ["ok", "offline", "failed"])
def test_every_allowed_finish_outcome_is_stored_as_given(db, outcome):
    run_id = start_engine_run(db, NOW, "all", 5)
    finish_engine_run(db, run_id, NOW + timedelta(minutes=9), outcome)
    row = run_row(db, run_id)
    assert row["outcome"] == outcome and from_iso(row["finished_at"]) == NOW + timedelta(minutes=9)


# -- finish_engine_run closes a row that is still running, exactly once


def test_finishing_an_unknown_run_is_refused_and_changes_no_row(db):
    running = start_engine_run(db, NOW, "due", 2)
    done = start_engine_run(db, NOW + HOUR, "due", 2)
    finish_engine_run(db, done, NOW + HOUR + HALF_HOUR, "ok", sources_ok=2)
    log_engine_tick(db, NOW + 2 * HOUR, "due", "busy")
    before = diary(db)
    unknown = running + done + 100
    with pytest.raises(ValueError):
        finish_engine_run(db, unknown, NOW + 3 * HOUR, "ok", sources_ok=1, items_new=1)
    with pytest.raises(ValueError):
        finish_engine_run(db, 0, NOW + 3 * HOUR, "failed", error="x")
    assert diary(db) == before
    assert db.execute("SELECT COUNT(*) FROM engine_runs WHERE engine_run_id = ?", (unknown,)).fetchone()[0] == 0


def test_finishing_in_an_empty_diary_is_refused(db):
    with pytest.raises(ValueError):
        finish_engine_run(db, 1, NOW, "ok")
    assert diary(db) == []


@pytest.mark.parametrize("first", ["ok", "offline", "failed"])
def test_a_run_that_is_already_finished_cannot_be_finished_again(db, first):
    run_id = start_engine_run(db, NOW, "due", 6)
    bystander = start_engine_run(db, NOW + HOUR, "due", 1)
    finish_engine_run(
        db,
        run_id,
        NOW + timedelta(minutes=4),
        first,
        sources_ok=5,
        sources_failed=1,
        sources_skipped=0,
        items_new=11,
        google_refusals=2,
        error="first word" if first != "ok" else None,
    )
    before = diary(db)
    for second in ("ok", "offline", "failed"):
        with pytest.raises(ValueError):
            finish_engine_run(
                db, run_id, NOW + HOUR, second, sources_ok=0, sources_failed=9, items_new=0, error="second word"
            )
    assert diary(db) == before  # the first answer stands: nothing was overwritten
    row = run_row(db, run_id)
    assert row["outcome"] == first and (row["sources_ok"], row["items_new"], row["google_refusals"]) == (5, 11, 2)
    assert from_iso(row["finished_at"]) == NOW + timedelta(minutes=4)
    assert run_row(db, bystander)["outcome"] == "running"


@pytest.mark.parametrize("tick_outcome", ["nothing_due", "busy", "failed"])
def test_a_tick_row_can_never_be_finished_as_a_run(db, tick_outcome):
    tick_id = log_engine_tick(
        db,
        NOW,
        "due",
        tick_outcome,
        sources_due=3,
        error="schedule file is broken" if tick_outcome == "failed" else None,
    )
    running = start_engine_run(db, NOW + HOUR, "due", 1)
    before = diary(db)
    for outcome in ("ok", "offline", "failed"):
        with pytest.raises(ValueError):
            finish_engine_run(db, tick_id, NOW + 2 * HOUR, outcome, sources_ok=3, items_new=5, error="late")
    assert diary(db) == before
    row = run_row(db, tick_id)
    assert row["outcome"] == tick_outcome and row["started_at"] == row["finished_at"] == utc_iso(NOW)
    assert run_row(db, running)["outcome"] == "running"


def test_a_refused_finish_leaves_other_tables_alone_too(db):
    fail(db, "S011", 3)
    run_id = start_engine_run(db, NOW, "due", 1)
    finish_engine_run(db, run_id, NOW + HALF_HOUR, "ok")
    before = other_tables(db)
    for bad_id in (run_id, run_id + 50):
        with pytest.raises(ValueError):
            finish_engine_run(db, bad_id, NOW + HOUR, "failed", error="x")
    assert other_tables(db) == before


# -- log_engine_tick keeps an error, and a failed tick is a real run

CHECK_FAILED = "feeds.csv could not be read: line 12 has too many columns"


def test_a_failed_tick_stores_its_error_and_the_moment_it_happened(db):
    tick_id = log_engine_tick(db, NOW, "due", "failed", error=CHECK_FAILED)
    row = run_row(db, tick_id)
    assert (row["mode"], row["outcome"], row["error"]) == ("due", "failed", CHECK_FAILED)
    assert row["started_at"] == row["finished_at"] == utc_iso(NOW)
    assert row["sources_due"] == 0 and all(row[c] is None for c in DIARY_COUNTS)

    positional = log_engine_tick(db, NOW + HALF_HOUR, "all", "failed", 4, "second failure")  # the signature order
    row = run_row(db, positional)
    assert (row["mode"], row["sources_due"], row["error"]) == ("all", 4, "second failure")


def test_a_tick_without_an_error_has_no_error(db):
    for outcome in ("nothing_due", "busy", "failed"):
        assert run_row(db, log_engine_tick(db, NOW, "due", outcome))["error"] is None


def test_a_failed_tick_is_listed_in_the_diary_with_its_error(db):
    log_engine_tick(db, NOW, "due", "nothing_due")
    failed = log_engine_tick(db, NOW + HALF_HOUR, "due", "failed", error=CHECK_FAILED)
    rows = engine_runs_since(db, NOW)
    assert [r["outcome"] for r in rows] == ["nothing_due", "failed"]
    assert rows[1]["engine_run_id"] == failed and rows[1]["error"] == CHECK_FAILED


def test_a_failed_tick_counts_as_the_last_run_but_nothing_due_and_busy_do_not(db):
    log_engine_tick(db, NOW, "due", "nothing_due")
    failed = log_engine_tick(db, NOW + HALF_HOUR, "due", "failed", error=CHECK_FAILED)
    log_engine_tick(db, NOW + HOUR, "due", "busy", sources_due=2)
    log_engine_tick(db, NOW + HOUR + HALF_HOUR, "due", "nothing_due")
    row = last_engine_run(db)
    assert row is not None
    assert row["engine_run_id"] == failed and row["outcome"] == "failed" and row["error"] == CHECK_FAILED


def test_a_failed_tick_only_diary_still_has_a_last_run(db):
    failed = log_engine_tick(db, NOW, "due", "failed", error=CHECK_FAILED)
    assert last_engine_run(db)["engine_run_id"] == failed


def test_the_last_run_is_whichever_of_a_real_run_and_a_failed_tick_came_later(db):
    run_id = start_engine_run(db, NOW, "due", 4)
    finish_engine_run(db, run_id, NOW + timedelta(minutes=3), "ok", sources_ok=4)
    log_engine_tick(db, NOW - HOUR, "due", "failed", error="old")
    assert last_engine_run(db)["engine_run_id"] == run_id  # the failed tick is older than the run
    newer_failed_tick = log_engine_tick(db, NOW + HOUR, "due", "failed", error="new")
    assert last_engine_run(db)["engine_run_id"] == newer_failed_tick
    same_instant_run = start_engine_run(db, NOW + HOUR, "due", 1)
    assert last_engine_run(db)["engine_run_id"] == same_instant_run  # tie: the later row


# -- first_engine_run_at


def test_first_engine_run_at_is_none_when_the_diary_is_empty(db):
    assert first_engine_run_at(db) is None


def test_first_engine_run_at_is_the_earliest_start_in_the_diary(db):
    start_engine_run(db, NOW, "due", 1)
    assert first_engine_run_at(db) == NOW
    start_engine_run(db, NOW + HOUR, "due", 1)
    assert first_engine_run_at(db) == NOW  # a later start changes nothing
    start_engine_run(db, NOW - HOUR, "all", 131)  # written last, started first: the time decides, not the row order
    assert first_engine_run_at(db) == NOW - HOUR


def test_first_engine_run_at_counts_ticks_of_every_kind(db):
    run_id = start_engine_run(db, NOW, "due", 1)
    finish_engine_run(db, run_id, NOW + HOUR, "ok")
    log_engine_tick(db, NOW - HALF_HOUR, "due", "nothing_due")
    assert first_engine_run_at(db) == NOW - HALF_HOUR
    log_engine_tick(db, NOW - HOUR, "due", "busy", sources_due=1)
    assert first_engine_run_at(db) == NOW - HOUR
    log_engine_tick(db, NOW - 2 * HOUR, "due", "failed", error=CHECK_FAILED)
    assert first_engine_run_at(db) == NOW - 2 * HOUR


def test_first_engine_run_at_is_the_start_not_the_finish_and_only_ticks_are_enough(db):
    log_engine_tick(db, NOW + HOUR, "due", "busy")
    assert first_engine_run_at(db) == NOW + HOUR
    run_id = start_engine_run(db, NOW + 2 * HOUR, "due", 1)
    finish_engine_run(db, run_id, NOW - 5 * HOUR, "ok")  # an absurd finish time must not be mistaken for a start
    assert first_engine_run_at(db) == NOW + HOUR


def test_first_engine_run_at_is_an_aware_utc_datetime(db):
    ist = timezone(timedelta(hours=5, minutes=30))
    start_engine_run(db, datetime(2026, 10, 4, 11, 30, tzinfo=ist), "all", 3)  # 06:00 UTC
    first = first_engine_run_at(db)
    assert isinstance(first, datetime)
    assert first.tzinfo is not None and first.utcoffset() == timedelta(0)
    assert first == NOW
    assert first == from_iso("2026-10-04T06:00:00+00:00")


def test_first_engine_run_at_ignores_fetch_history_and_other_tables(db):
    fail(db, "S011", 3, at=NOW - 10 * HOUR)  # fetches long before the diary began are not engine runs
    assert first_engine_run_at(db) is None
    before = other_tables(db)
    start_engine_run(db, NOW, "due", 1)
    assert first_engine_run_at(db) == NOW
    assert other_tables(db) == before


# -- last_attempted: only sources that have fetch rows, and an inactive source that was tried counts


def test_last_attempted_includes_an_inactive_source_that_was_tried(db):
    record_fetch(db, FetchOutcome("S011", NATIVE, "ok"), NOW)
    fail(db, "S025", 2, route_type="Web page monitor", at=NOW - HOUR)
    db.execute("UPDATE sources SET active = 0 WHERE source_id IN ('S025', 'S019')")  # S019 was never fetched
    db.commit()
    assert last_attempted(db) == {"S011": NOW, "S025": NOW - HOUR}  # the tried inactive one is in, the untried is not
    assert "S019" not in last_attempted(db)


def test_last_attempted_takes_the_latest_try_of_an_inactive_source(db):
    record_fetch(db, FetchOutcome("S025", "Web page monitor", "ok"), NOW - 5 * HOUR)
    record_fetch(db, FetchOutcome("S025", "Web page monitor", "error", reason="HTTP 404"), NOW - HOUR)
    db.execute("UPDATE sources SET active = 0 WHERE source_id = 'S025'")
    db.commit()
    assert last_attempted(db) == {"S025": NOW - HOUR}
