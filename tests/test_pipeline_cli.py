import asyncio
import json
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
    conn.execute("UPDATE sources SET fetch_url = ? WHERE source_id = 'S011'", (DH_FEED,))
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
    s = run(db, ["S011", gn, "S112"], handler)
    assert s.statuses == {"ok": 2, "skipped": 1}
    assert s.items_new == 2 and s.dropped == {"older than 7 days": 1, "blocklisted (bengalurumetro.in)": 1}
    assert s.stories_new == 1 and s.tagged == 1  # DH and The Hindu are one story, tagged tunnel road
    story = db.execute("SELECT * FROM story_clusters").fetchone()
    assert story["source_count"] == 2 and json.loads(story["sources"]) == sorted(["S011", gn])
    topics = {r[0] for r in db.execute("SELECT topic_id FROM item_topics WHERE match_method = 'keyword'")}
    assert "O06" in topics
    urls = {r[0] for r in db.execute("SELECT url FROM items")}
    assert "https://www.thehindu.com/news/tunnel.ece" in urls  # Google News link resolved
    assert db.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 3
    assert db.execute("SELECT etag FROM sources WHERE source_id = 'S011'").fetchone()[0] == '"v1"'
    # scored: O06 tunnel road (High 30, Bangalore 25) + 2 sources (5) + best tier Official (10), no AI points yet
    scored = db.execute("SELECT relevance_score, label FROM story_clusters").fetchone()
    assert scored["relevance_score"] == 30 + 25 + 5 + 10 and scored["label"] == "High"
    assert sum(s.labels.values()) == 1


def test_a_second_run_adds_nothing_twice(db):
    handler, _ = network()
    gn = gn_source_id(db)
    run(db, ["S011", gn], handler)
    again = run(db, ["S011", gn], handler, now=NOW + timedelta(minutes=30))
    assert again.items_new == 0 and again.stories_new == 0
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM story_clusters").fetchone()[0] == 1


def test_a_failing_source_is_recorded_and_the_run_continues(db):
    handler, _ = network()
    db.execute("UPDATE sources SET fetch_url = 'https://down.example/feed' WHERE source_id = 'S034'")
    db.commit()
    s = run(db, ["S011", "S034"], handler)
    assert s.statuses == {"ok": 1, "error": 1}
    row = db.execute("SELECT consecutive_failures, last_status FROM sources WHERE source_id = 'S034'").fetchone()
    assert row[0] == 1 and row[1] == "error: HTTP 404"


# ---- health checks


def outcome(status="ok", entries=None, **kw):
    return FetchOutcome("S011", kw.pop("route_type", "Native publisher RSS/Atom"), status, entries=entries or [], **kw)


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
    backup = health.check(outcome("skipped", reason="backup is not a Google News feed: 'https://x.com/a'"), now)
    assert "news.google.com/rss/search" in backup.fix


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
    feeds = tmp_path / "feeds.csv"
    rows = (repo_root / "feeds.csv").read_text(encoding="utf-8").splitlines()
    feeds.write_text("\n".join([rows[0]] + [r for r in rows if r.startswith(("S011,", "S108,", "S121,"))]) + "\n")
    text = feeds.read_text().replace(
        rows[[i for i, r in enumerate(rows) if r.startswith("S011,")][0]].split(",")[4], DH_FEED
    )
    feeds.write_text(text)
    r = CliRunner().invoke(cli.app, ["test-feeds", "--feeds", str(feeds), "--out", str(tmp_path / "reports")])
    assert r.exit_code == 0, r.output
    assert "1 passed, 2 failed" in r.output
    assert "S108" in r.output and "backup_google_news_url" in r.output
    assert "S121" in r.output and "channel_id" in r.output
    reports = sorted(p.suffix for p in (tmp_path / "reports").iterdir())
    assert reports == [".csv", ".md"]


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
    assert both.exit_code == 1 and "not both" in both.output
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
    conn.execute("UPDATE sources SET fetch_url = ? WHERE source_id = 'S011'", (DH_FEED,))
    conn.commit()
    gn = gn_source_id(conn)
    asyncio.run(run_fetch(conn, store.load_sources(conn, ["S011", gn]), client=fake_client(handler)))
    r = runner.invoke(cli.app, ["stories", "--db", db_path])
    assert r.exit_code == 0, r.output
    assert "BBMP floats tender for tunnel road" in r.output and " 70 High" in r.output and "[awaiting AI]" in r.output
