import json
from datetime import datetime, timedelta, timezone

import pytest

from riffi_ingest.db import connect
from riffi_ingest.db.connection import from_iso, utc_iso
from riffi_ingest.db.importers import import_sources, import_topics
from riffi_ingest.db.store import (
    load_gnews_cache,
    load_recent_clusters,
    load_snapshots,
    load_validators,
    prune,
    record_fetch,
    save_gnews_cache,
    save_run,
    save_tags,
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
