import asyncio
import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from typer.testing import CliRunner

from riffi_ingest import cli, health, pipeline
from riffi_ingest.db import connect, store
from riffi_ingest.db.importers import import_sources, import_topics
from riffi_ingest.fetchers.outcome import FetchOutcome
from riffi_ingest.fetchers.parse import Entry
from riffi_ingest.pipeline import run_fetch
from riffi_ingest.runlock import run_lock
from tests.feedtools import fake_client, old_style, rfc822, rss

NOW = datetime.now(timezone.utc).replace(microsecond=0)
DH_FEED = "https://www.deccanherald.com/rss/bengaluru"


def network(tunnel_title="BBMP floats tender for tunnel road"):
    gn_link = f"https://news.google.com/rss/articles/{old_style('https://www.thehindu.com/news/tunnel.ece')}?oc=5"
    feeds = {
        "www.deccanherald.com": rss(
            [
                {
                    "title": tunnel_title,
                    "link": "https://www.deccanherald.com/city/tunnel-1",
                    "date": rfc822(NOW - timedelta(hours=2)),
                },
                {
                    "title": "Old story",
                    "link": "https://www.deccanherald.com/city/old",
                    "date": rfc822(NOW - timedelta(days=9)),
                },
                {"title": "Copycat claim", "link": "https://bengalurumetro.in/fake", "date": rfc822(NOW)},
            ],
            feed_title="DH Bengaluru",
        ),
        "news.google.com": (
            '<?xml version="1.0"?><rss version="2.0"><channel><title>GN</title><item>'
            f"<title>Tunnel road tender floated by BBMP - The Hindu</title><link>{gn_link}</link>"
            f"<pubDate>{rfc822(NOW - timedelta(hours=1))}</pubDate>"
            '<source url="https://www.thehindu.com">The Hindu</source></item></channel></rss>'
        ).encode(),
    }
    calls = []

    def handler(request):
        calls.append(request.url.host)
        body = feeds.get(request.url.host)
        return httpx.Response(200, content=body, headers={"ETag": '"v1"'}) if body else httpx.Response(404)

    return handler, calls


@pytest.fixture
def db(tmp_path, repo_root):
    conn = connect(tmp_path / "engine.db")
    import_sources(conn, repo_root / "feeds.csv")
    import_topics(conn, repo_root / "topics.csv")
    # two test sources standing in for real rows
    conn.execute("UPDATE sources SET fetch_url = ? WHERE source_id = 'S049'", (DH_FEED,))
    conn.commit()
    return conn


def sources_for(conn, ids):
    return store.load_sources(conn, ids)


def gn_source_id(conn):
    return conn.execute(
        "SELECT source_id FROM sources WHERE route_type = 'Google News RSS' ORDER BY source_id"
    ).fetchone()[0]


def run(conn, ids, handler, now=NOW):
    async def go():
        async with fake_client(handler) as client:
            return await run_fetch(conn, sources_for(conn, ids), client=client, now=now)

    return asyncio.run(go())


def test_one_full_cycle_stores_clean_tagged_stories(db):
    handler, _ = network()
    gn = gn_source_id(db)
    s = run(db, ["S049", gn, "S112"], handler)
    assert s.statuses == {"ok": 2, "skipped": 1}
    assert s.items_new == 2 and s.dropped == {"older than 7 days": 1, "blocklisted (bengalurumetro.in)": 1}
    assert s.stories_new == 1 and s.tagged == 1  # DH and The Hindu are one story, tagged tunnel road
    story = db.execute("SELECT * FROM story_clusters").fetchone()
    assert story["source_count"] == 2 and json.loads(story["sources"]) == sorted(["S049", gn])
    topics = {r[0] for r in db.execute("SELECT topic_id FROM item_topics WHERE match_method = 'keyword'")}
    assert "O06" in topics
    urls = {r[0] for r in db.execute("SELECT url FROM items")}
    assert "https://www.thehindu.com/news/tunnel.ece" in urls  # Google News link resolved
    assert db.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 3
    assert db.execute("SELECT etag FROM sources WHERE source_id = 'S049'").fetchone()[0] == '"v1"'
    # scored: O06 tunnel road (High 30, Bangalore 25) + 2 sources (5) + best tier Official (10), no AI points yet
    scored = db.execute("SELECT relevance_score, label FROM story_clusters").fetchone()
    assert scored["relevance_score"] == 30 + 25 + 5 + 10 and scored["label"] == "High"
    assert sum(s.labels.values()) == 1


def accounted(s):
    return s.items_new + sum(s.dropped.values()) + s.already_stored + s.repeated


def test_a_second_run_adds_nothing_twice(db):
    handler, _ = network()
    gn = gn_source_id(db)
    first = run(db, ["S049", gn], handler)
    assert (first.entries, first.already_stored, first.repeated) == (4, 0, 0) and accounted(first) == 4
    again = run(db, ["S049", gn], handler, now=NOW + timedelta(minutes=30))
    assert again.items_new == 0 and again.stories_new == 0
    assert (again.entries, again.already_stored, again.repeated) == (4, 2, 0) and accounted(again) == 4
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM story_clusters").fetchone()[0] == 1


def test_an_article_listed_twice_in_one_run_is_counted_as_repeated(db):
    one = {"title": "BBMP floats tender for tunnel road", "link": "https://www.deccanherald.com/city/tunnel-1"}
    body = rss([{**one, "date": rfc822(NOW - timedelta(hours=2))}] * 2, feed_title="DH Bengaluru")
    s = run(db, ["S049"], lambda request: httpx.Response(200, content=body))
    assert (s.entries, s.items_new, s.already_stored, s.repeated) == (2, 1, 0, 1) and accounted(s) == 2


def test_a_failing_source_is_recorded_and_the_run_continues(db):
    handler, _ = network()
    db.execute("UPDATE sources SET fetch_url = 'https://down.example/feed' WHERE source_id = 'S034'")
    db.commit()
    s = run(db, ["S049", "S034"], handler)
    assert s.statuses == {"ok": 1, "error": 1}
    row = db.execute("SELECT consecutive_failures, last_status FROM sources WHERE source_id = 'S034'").fetchone()
    assert row[0] == 1 and row[1] == "error: HTTP 404"


# ---- health checks


def outcome(status="ok", entries=None, **kw):
    return FetchOutcome("S049", kw.pop("route_type", "Native publisher RSS/Atom"), status, entries=entries or [], **kw)


def test_health_checks_and_fixes():
    now = NOW
    fresh = [Entry(title="a", link="https://a.in/1", published=now - timedelta(hours=1))]
    assert health.check(outcome(entries=fresh), now).passed
    stale = health.check(
        outcome(entries=[Entry(title="a", link="https://a.in/1", published=now - timedelta(days=9))]), now
    )
    assert not stale.passed and "9 days old" in stale.problems[0]
    empty_gn = health.check(outcome(url="https://news.google.com/rss/search?q=x"), now)
    assert not empty_gn.passed and "Google News query finds nothing" in empty_gn.fix
    undated = health.check(outcome(entries=[Entry(title="a", link="https://a.in/1")]), now)
    assert not undated.passed and "missing date" in undated.problems
    assert "dates them at fetch time" in undated.fix
    untitled = health.check(outcome(entries=[Entry(link="https://a.in/1", published=now)]), now)
    assert not untitled.passed and "missing title" in untitled.problems and "fetch time" not in untitled.fix
    assert "blocks automated requests" in health.check(outcome("error", reason="HTTP 403", http_status=403), now).fix
    assert "moved" in health.check(outcome("error", reason="HTTP 404", http_status=404), now).fix
    assert "RSS link" in health.check(outcome("error", reason="answered with an HTML page, not a feed"), now).fix
    tg = outcome("error", reason="HTTP 429", http_status=429, route_type="RSSHub Telegram", url="https://rsshub.app/x")
    assert "self-host RSSHub" in health.check(tg, now).fix
    yt = health.check(outcome("skipped", reason="needs a YouTube channel ID (fetch_url is 'Needs channel ID')"), now)
    assert not yt.passed and "channel_id=" in yt.fix
    assert health.check(outcome("skipped", reason="manual source"), now).passed
    from riffi_ingest.fetchers.outcome import PageSnapshot

    page = outcome(route_type="Web page monitor", snapshot=PageSnapshot("h", "Notices\nFares revised"))
    assert health.check(page, now).passed
    blank = health.check(outcome(route_type="Web page monitor", snapshot=PageSnapshot("h", "")), now)
    assert not blank.passed and "JavaScript" in blank.fix
    tg404 = outcome(
        "error", reason="HTTP 404", http_status=404, route_type="RSSHub Telegram", url="https://rsshub.app/x"
    )
    assert "cannot find this channel" in health.check(tg404, now).fix
    page = {"route_type": "RSSHub Telegram", "url": "https://t.me/s/x"}
    assert "t.me is refusing" in health.check(outcome("error", reason="HTTP 429", http_status=429, **page), now).fix
    no_posts = outcome("error", reason="the channel page shows no posts (not public, ...)", **page)
    assert "public channel" in health.check(no_posts, now).fix
    backup = health.check(outcome("skipped", reason="backup is not a Google News feed: 'https://x.com/a'"), now)
    assert "news.google.com/rss/search" in backup.fix


def test_a_telegram_posts_text_counts_as_its_title():
    now = NOW
    tg = {"route_type": "RSSHub Telegram", "url": "https://t.me/s/x"}
    post = Entry(link="https://t.me/x/1", published=now - timedelta(hours=1), summary="Metro fares revised. More")
    h = health.check(outcome(entries=[post], **tg), now)
    assert h.passed and h.fields[:3] == ["title", "link", "date"]
    blank = Entry(link="https://t.me/x/2", published=now - timedelta(hours=1), summary="  ")
    h = health.check(outcome(entries=[blank], **tg), now)
    assert not h.passed and "missing title" in h.problems
    rss = health.check(outcome(entries=[post]), now)  # only a Telegram post gets a title from its text
    assert not rss.passed and "missing title" in rss.problems


# ---- the commands


def test_cli_import_commands(tmp_path, repo_root):
    db_path = tmp_path / "engine.db"
    runner = CliRunner()
    r = runner.invoke(cli.app, ["import-sources", "--db", str(db_path), "--feeds", str(repo_root / "feeds.csv")])
    assert r.exit_code == 0 and "131 added" in r.output
    r = runner.invoke(cli.app, ["import-topics", "--db", str(db_path), "--topics", str(repo_root / "topics.csv")])
    assert r.exit_code == 0 and "151 added" in r.output
    r = runner.invoke(cli.app, ["import-sources", "--db", str(db_path), "--feeds", str(repo_root / "feeds.csv")])
    assert "0 added, 131 updated" in r.output


def test_cli_test_feeds_writes_a_report(tmp_path, repo_root, monkeypatch):
    handler, _ = network()
    monkeypatch.setattr(cli, "PoliteClient", lambda: fake_client(handler))
    with open(repo_root / "feeds.csv", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = {r["source_id"]: r for r in reader}
    # one feed that answers, and S108 / S121 as they stood before their fixes were supplied (5 Oct 2026)
    picked = [
        {**rows["S049"], "fetch_url": DH_FEED},
        {**rows["S108"], "backup_google_news_url": ""},
        {**rows["S121"], "fetch_url": "Needs channel ID"},
    ]
    feeds = tmp_path / "feeds.csv"
    with open(feeds, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=reader.fieldnames)
        writer.writeheader()
        writer.writerows(picked)
    r = CliRunner().invoke(cli.app, ["test-feeds", "--feeds", str(feeds), "--out", str(tmp_path / "reports")])
    assert r.exit_code == 0, r.output
    assert "1 passed, 2 failed" in r.output
    assert "S108" in r.output and "backup_google_news_url" in r.output
    assert "S121" in r.output and "channel_id" in r.output
    reports = sorted(p.suffix for p in (tmp_path / "reports").iterdir())
    assert reports == [".csv", ".md"]
    again = CliRunner().invoke(cli.app, ["test-feeds", "--feeds", str(feeds), "--out", str(tmp_path / "reports")])
    assert again.exit_code == 0, again.output
    assert len(list((tmp_path / "reports").iterdir())) == 4  # a second run keeps the first run's report


def test_test_feeds_reports_in_the_same_second_get_their_own_names(tmp_path):
    out = tmp_path / "reports"
    stamp, csv_path, md_path = cli._report_paths(out, NOW)
    assert stamp == NOW.astimezone(cli.IST).strftime("%Y-%m-%d-%H%M%S")
    assert (csv_path.name, md_path.name) == (f"{stamp}.csv", f"{stamp}.md")
    csv_path.write_text("x")
    assert cli._report_paths(out, NOW)[0] == f"{stamp}-2"
    (out / f"{stamp}-2.md").write_text("x")  # either file taken is enough to move on
    assert cli._report_paths(out, NOW)[0] == f"{stamp}-3"


def test_cli_fetch_needs_a_choice_and_imports_on_first_run(tmp_path, repo_root, monkeypatch):
    runner = CliRunner()
    db_path = tmp_path / "engine.db"
    assert runner.invoke(cli.app, ["fetch", "--db", str(db_path)]).exit_code != 0
    handler, _ = network()
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(handler))
    r = runner.invoke(
        cli.app, ["fetch", "--source", "s112", "--db", str(db_path), "--feeds", str(repo_root / "feeds.csv")]
    )
    assert r.exit_code == 0, r.output
    assert "First run: imported 131 sources" in r.output and "imported 151 topics" in r.output
    assert "1 skipped" in r.output


def test_a_run_without_topics_stops_with_a_clear_message(tmp_path, repo_root):
    conn = connect(tmp_path / "e.db")
    import_sources(conn, repo_root / "feeds.csv")
    handler, _ = network()
    with pytest.raises(RuntimeError, match="import-topics"):
        asyncio.run(run_fetch(conn, store.load_sources(conn, ["S112"]), client=fake_client(handler)))


def test_cli_rejects_unclear_requests(tmp_path, repo_root):
    runner = CliRunner()
    db_path = str(tmp_path / "engine.db")
    feeds = str(repo_root / "feeds.csv")
    both = runner.invoke(cli.app, ["fetch", "--all", "--source", "S004", "--db", db_path, "--feeds", feeds])
    assert both.exit_code == 1 and "exactly one of" in both.output
    due_and_all = runner.invoke(cli.app, ["fetch", "--due", "--all", "--db", db_path, "--feeds", feeds])
    assert due_and_all.exit_code == 1 and "exactly one of" in due_and_all.output
    unknown = runner.invoke(cli.app, ["fetch", "--source", "S999", "--db", db_path, "--feeds", feeds])
    assert unknown.exit_code == 1 and "no such source id: S999" in unknown.output
    missing = runner.invoke(cli.app, ["test-feeds", "--feeds", str(tmp_path / "nope.csv")])
    assert missing.exit_code == 1 and "cannot find" in missing.output
    unknown_test = runner.invoke(cli.app, ["test-feeds", "--source", "s999", "--feeds", feeds])
    assert unknown_test.exit_code == 1 and "S999" in unknown_test.output


def test_cli_shows_progress_and_an_estimate(tmp_path, repo_root, monkeypatch):
    handler, _ = network()
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(handler))
    r = CliRunner().invoke(
        cli.app,
        [
            "fetch",
            "--source",
            "S112",
            "--source",
            "s108",
            "--db",
            str(tmp_path / "e.db"),
            "--feeds",
            str(repo_root / "feeds.csv"),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "expect up to about" in r.output and "[  2/2]" in r.output


def test_only_one_fetch_runs_at_a_time(tmp_path):
    from riffi_ingest.runlock import AlreadyRunning, run_lock

    lock = tmp_path / "fetch.lock"
    with run_lock(lock):
        with pytest.raises(AlreadyRunning):
            with run_lock(lock):
                pass
    with run_lock(lock):  # released afterwards
        pass


def test_cli_stories_lists_the_best_first(tmp_path, repo_root, monkeypatch):
    handler, _ = network()
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(handler))
    runner = CliRunner()
    db_path = str(tmp_path / "e.db")
    empty = runner.invoke(cli.app, ["stories", "--db", db_path])
    assert "No stories" in empty.output
    conn = connect(db_path)
    import_sources(conn, repo_root / "feeds.csv")
    import_topics(conn, repo_root / "topics.csv")
    conn.execute("UPDATE sources SET fetch_url = ? WHERE source_id = 'S049'", (DH_FEED,))
    conn.commit()
    gn = gn_source_id(conn)
    asyncio.run(run_fetch(conn, store.load_sources(conn, ["S049", gn]), client=fake_client(handler)))
    r = runner.invoke(cli.app, ["stories", "--db", db_path])
    assert r.exit_code == 0, r.output
    assert "BBMP floats tender for tunnel road" in r.output and " 70 High" in r.output and "[awaiting AI]" in r.output


# ---- no internet, and Google saying no


def unreachable(request):
    raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request)


def test_no_internet_blames_no_source(db):
    s = run(db, ["S049", gn_source_id(db), "S112"], unreachable)
    assert s.offline and s.statuses == {"error": 2, "skipped": 1}
    assert db.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 0  # so every source is still due next tick
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    assert db.execute("SELECT MAX(consecutive_failures) FROM sources").fetchone()[0] == 0


def test_one_unreachable_site_is_that_sites_problem(db):
    s = run(db, ["S049"], unreachable)
    assert not s.offline and s.statuses == {"error": 1}
    assert db.execute("SELECT consecutive_failures FROM sources WHERE source_id = 'S049'").fetchone()[0] == 1


def test_one_site_answering_means_the_internet_is_up(db):
    handler, _ = network()

    def google_down(request):
        if request.url.host == "news.google.com":
            raise httpx.ConnectError("connection refused", request=request)
        return handler(request)

    s = run(db, ["S049", gn_source_id(db)], google_down)
    assert not s.offline and s.statuses == {"ok": 1, "error": 1}


def test_google_refusals_are_counted(db):
    handler, _ = network()

    def refusing(request):
        return httpx.Response(429) if request.url.host == "news.google.com" else handler(request)

    s = run(db, ["S049", gn_source_id(db)], refusing)
    assert s.google_refusals == 1 and not s.offline


# ---- fetch --due: what Windows' timer runs

ROUTES = (
    "Google News RSS",
    "X/Instagram via RSS.app",
    "Native publisher RSS/Atom",
    "YouTube Atom",
    "RSSHub Telegram",
    "Web page monitor",
    "Manual",
)


def schedule_file(tmp_path, speeds):
    """Every route type 'never', plus per-source `speeds`, so a test fetches exactly the sources it names."""
    lines = ["early_minutes: 5", "by_route:", *(f"  {r}: never" for r in ROUTES), "sources:"]
    lines += [f"  {source_id}: {speed}" for source_id, speed in speeds.items()]
    path = tmp_path / "schedule.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def prepared_db(tmp_path, repo_root):
    db_path = tmp_path / "engine.db"
    conn = connect(db_path)
    import_sources(conn, repo_root / "feeds.csv")
    import_topics(conn, repo_root / "topics.csv")
    conn.execute("UPDATE sources SET fetch_url = ? WHERE source_id = 'S049'", (DH_FEED,))
    conn.commit()
    return db_path, conn


def fetch_due(db_path, schedule):
    return CliRunner().invoke(cli.app, ["fetch", "--due", "--db", str(db_path), "--schedule", str(schedule)])


def diary(conn):
    return conn.execute("SELECT * FROM engine_runs ORDER BY engine_run_id").fetchall()


def test_fetch_due_fetches_what_is_due_and_writes_the_diary(tmp_path, repo_root, monkeypatch):
    handler, calls = network()
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(handler))
    db_path, conn = prepared_db(tmp_path, repo_root)
    schedule = schedule_file(tmp_path, {"S049": "2h"})
    r = fetch_due(db_path, schedule)
    assert r.exit_code == 0, r.output
    assert calls == ["www.deccanherald.com"]  # only the one source that is due
    row = diary(conn)[0]
    assert (row["mode"], row["outcome"], row["sources_due"], row["sources_ok"], row["items_new"]) == (
        "due",
        "ok",
        1,
        1,
        1,
    )
    assert row["finished_at"] is not None
    assert conn.execute("SELECT started_at FROM fetch_runs").fetchone()[0] == row["started_at"]
    log = (tmp_path / "engine.log").read_text(encoding="utf-8")
    assert " due " in log and " ok " in log and "new items 1" in log
    again = fetch_due(db_path, schedule)  # straight away: nothing is due for another 2 hours
    assert again.exit_code == 0 and "Nothing is due" in again.output
    assert [x["outcome"] for x in diary(conn)] == ["ok", "nothing_due"]
    assert "nothing_due" in (tmp_path / "engine.log").read_text(encoding="utf-8")


def test_a_check_while_a_run_is_going_is_skipped_and_noted(tmp_path, repo_root):
    db_path, conn = prepared_db(tmp_path, repo_root)
    schedule = schedule_file(tmp_path, {"S049": "2h"})
    with run_lock(tmp_path / "fetch.lock"):
        r = fetch_due(db_path, schedule)
        by_hand = CliRunner().invoke(cli.app, ["fetch", "--all", "--db", str(db_path)])
    assert r.exit_code == 0 and "still running" in r.output
    assert by_hand.exit_code == 1 and "already running" in by_hand.output
    assert [(x["outcome"], x["sources_due"]) for x in diary(conn)] == [("busy", 1)]
    assert " busy " in (tmp_path / "engine.log").read_text(encoding="utf-8")


def test_a_crashed_run_is_marked_failed_with_its_reason(tmp_path, repo_root, monkeypatch):
    async def disk_full(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cli, "run_fetch", disk_full)
    db_path, conn = prepared_db(tmp_path, repo_root)
    r = fetch_due(db_path, schedule_file(tmp_path, {"S049": "2h"}))
    assert r.exit_code == 1 and "disk full" in r.output
    rows = diary(conn)
    assert len(rows) == 1 and rows[0]["outcome"] == "failed" and rows[0]["finished_at"]
    assert "RuntimeError: disk full" in rows[0]["error"]
    assert "RuntimeError: disk full" in (tmp_path / "engine.log").read_text(encoding="utf-8")


def test_an_interrupted_run_is_closed_as_failed(tmp_path, repo_root, monkeypatch):
    async def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_fetch", interrupted)
    _, conn = prepared_db(tmp_path, repo_root)
    with pytest.raises(KeyboardInterrupt):
        cli._run_and_record(conn, store.load_sources(conn, ["S049"]), "all", NOW, tmp_path / "engine.log")
    row = diary(conn)[0]
    assert row["outcome"] == "failed" and row["error"].startswith("KeyboardInterrupt") and row["finished_at"]


def test_no_internet_is_recorded_as_offline(tmp_path, repo_root, monkeypatch):
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(unreachable))
    db_path, conn = prepared_db(tmp_path, repo_root)
    r = fetch_due(db_path, schedule_file(tmp_path, {"S049": "2h", gn_source_id(conn): "2h"}))
    assert r.exit_code == 0 and "No internet" in r.output
    assert [(x["outcome"], x["sources_failed"]) for x in diary(conn)] == [("offline", 2)]
    assert conn.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 0
    assert " offline " in (tmp_path / "engine.log").read_text(encoding="utf-8")


def test_runs_by_hand_are_in_the_diary_too(tmp_path, repo_root, monkeypatch):
    handler, _ = network()
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(handler))
    db_path, conn = prepared_db(tmp_path, repo_root)
    r = CliRunner().invoke(cli.app, ["fetch", "--source", "S049", "--db", str(db_path)])
    assert r.exit_code == 0, r.output
    assert [(x["mode"], x["outcome"]) for x in diary(conn)] == [("source", "ok")]


def test_a_broken_or_missing_schedule_leaves_a_failed_row(tmp_path, repo_root):
    db_path, conn = prepared_db(tmp_path, repo_root)
    bad = tmp_path / "schedule.yaml"
    bad.write_text("by_route:\n  Google News RSS: fortnightly\n", encoding="utf-8")
    r = fetch_due(db_path, bad)
    assert r.exit_code == 1 and "fortnightly" in r.output
    missing = fetch_due(db_path, tmp_path / "nope.yaml")
    assert missing.exit_code == 1 and "nope.yaml" in missing.output
    rows = diary(conn)
    assert [x["outcome"] for x in rows] == ["failed", "failed"]
    assert "fortnightly" in rows[0]["error"] and "nope.yaml" in rows[1]["error"]
    log = (tmp_path / "engine.log").read_text(encoding="utf-8")
    assert "fortnightly" in log and "nope.yaml" in log


def test_a_quiet_check_with_a_locked_database_still_leaves_a_log_line(tmp_path, repo_root, monkeypatch):
    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    db_path, _ = prepared_db(tmp_path, repo_root)
    monkeypatch.setattr(store, "log_engine_tick", locked)
    r = fetch_due(db_path, schedule_file(tmp_path, {}))  # every source 'never': nothing is due
    assert r.exit_code == 0 and "Nothing is due" in r.output
    log = (tmp_path / "engine.log").read_text(encoding="utf-8")
    assert "nothing_due" in log and "could not write the diary row: OperationalError: database is locked" in log


def test_a_run_that_cannot_start_is_recorded(tmp_path, repo_root, monkeypatch):
    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    db_path, conn = prepared_db(tmp_path, repo_root)
    monkeypatch.setattr(store, "start_engine_run", locked)
    r = fetch_due(db_path, schedule_file(tmp_path, {"S049": "2h"}))
    assert r.exit_code == 1 and "could not start" in r.output
    rows = diary(conn)
    assert [x["outcome"] for x in rows] == ["failed"] and "database is locked" in rows[0]["error"]
    assert "database is locked" in (tmp_path / "engine.log").read_text(encoding="utf-8")


def test_a_log_that_cannot_be_written_does_not_lose_the_diary_row(tmp_path, repo_root, monkeypatch):
    (tmp_path / "engine.log").mkdir()  # opening a directory for append raises: the log cannot be written
    handler, _ = network()
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(handler))
    db_path, conn = prepared_db(tmp_path, repo_root)
    r = CliRunner().invoke(cli.app, ["fetch", "--source", "S049", "--db", str(db_path)])
    assert r.exit_code == 0, r.output
    rows = diary(conn)
    assert [(x["mode"], x["outcome"]) for x in rows] == [("source", "ok")]
    assert rows[0]["finished_at"] is not None  # the row was closed, not left 'running'


def test_a_quiet_check_with_a_log_that_cannot_be_written_still_leaves_its_diary_row(tmp_path, repo_root):
    (tmp_path / "engine.log").mkdir()
    db_path, conn = prepared_db(tmp_path, repo_root)
    r = fetch_due(db_path, schedule_file(tmp_path, {}))  # every source 'never': nothing is due
    assert r.exit_code == 0 and "Nothing is due" in r.output
    assert [x["outcome"] for x in diary(conn)] == ["nothing_due"]


def test_a_database_that_will_not_open_still_leaves_a_log_line(tmp_path, monkeypatch):
    def unopenable(*args, **kwargs):
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(cli, "connect", unopenable)
    r = fetch_due(tmp_path / "engine.db", schedule_file(tmp_path, {}))
    assert r.exit_code == 1 and "could not start" in r.output
    log = (tmp_path / "engine.log").read_text(encoding="utf-8")
    assert " failed " in log and "unable to open database file" in log


def test_after_a_clock_jump_a_source_is_fetched_once_then_keeps_its_speed(tmp_path, repo_root, monkeypatch):
    handler, calls = network()
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(handler))
    db_path, conn = prepared_db(tmp_path, repo_root)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO fetch_runs (source_id, started_at, status) VALUES ('S049', ?, 'ok')", (future,))
    conn.commit()
    schedule = schedule_file(tmp_path, {"S049": "2h"})
    assert fetch_due(db_path, schedule).exit_code == 0  # the stamp from a wrong clock is ignored: due now
    assert fetch_due(db_path, schedule).exit_code == 0  # then normal speed: not due again for 2 hours
    assert calls == ["www.deccanherald.com"]
    assert [x["outcome"] for x in diary(conn)] == ["ok", "nothing_due"]


# ---- the test safety net


def test_tests_never_use_the_real_database(tmp_path):
    from riffi_ingest.db.connection import DEFAULT_DB_PATH, default_db_path

    assert default_db_path() != DEFAULT_DB_PATH and default_db_path().is_relative_to(tmp_path)
