import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import yaml
from conftest import rss

from feedkit.net import PoliteClient
from feedkit.parse import Entry, iso
from fetcher import config as cfg
from fetcher import health as health_mod
from fetcher.api import get_fresh_items
from fetcher.db import DB
from fetcher.dedupe import Clusterer
from fetcher.normalize import canonical_url, clean_title, normalize_entry
from fetcher.poller import Poller
from fetcher.reddit_oauth import parse_listing

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def rfc822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


# ------------------------------------------------------------------ URLs


def test_canonical_url_strips_tracking_and_amp():
    assert (
        canonical_url("https://Inc42.com/buzz/x/?utm_source=rss&utm_medium=feed&id=7")
        == "https://inc42.com/buzz/x?id=7"
    )
    assert (
        canonical_url("https://www.thenewsminute.com/amp/story/karnataka/abc")
        == "https://www.thenewsminute.com/karnataka/abc"
    )
    assert (
        canonical_url("https://www.deccanherald.com/amp/story/india%2Fkarnataka%2Fbengaluru%2Fgba-x")
        == "https://www.deccanherald.com/india/karnataka/bengaluru/gba-x"
    )
    assert (
        canonical_url("https://bangaloremirror.indiatimes.com/bangalore/others/in-brief/amp_articleshow/1.cms")
        == "https://bangaloremirror.indiatimes.com/bangalore/others/in-brief/articleshow/1.cms"
    )
    assert (
        canonical_url("http://m.economictimes.com/tech/x.cms?from=rss#top")
        == "https://economictimes.indiatimes.com/tech/x.cms"
    )
    # legitimate ids survive
    assert (
        canonical_url("https://pib.gov.in/PressReleasePage.aspx?PRID=2315309")
        == "https://pib.gov.in/PressReleasePage.aspx?PRID=2315309"
    )


def test_clean_title_drops_google_news_suffix():
    assert clean_title("Metro fares go up - Deccan Herald", "Deccan Herald") == "Metro fares go up"


# ------------------------------------------------------------------ dedupe and clustering


def _feed(**kw):
    base = dict(id="T1", name="Test Paper", url="https://a.in/feed", subject_area="Bangalore & Karnataka")
    base.update(kw)
    return cfg.Feed(**base)


def _item(title, link, feed=None, published=None):
    entry = Entry(title=title, link=link, published=published or NOW - timedelta(hours=1), summary="teaser")
    return normalize_entry(entry, feed=feed or _feed(), fetched=NOW)


def test_clusters_near_duplicate_titles_and_skips_same_url():
    db = DB(":memory:")
    c = Clusterer(db, threshold=90, window_hours=48, now=NOW)
    a_added, a_cluster = c.add(_item("Bengaluru metro fares to rise by 10% from October", "https://a.in/1"))
    b_added, b_cluster = c.add(
        _item("Bengaluru metro fares to rise by 10% from October!", "https://b.in/9", _feed(id="T2", name="Other"))
    )
    dup_added, _ = c.add(_item("Totally different words", "https://a.in/1?utm_source=x"))
    other_added, other_cluster = c.add(_item("Karnataka cabinet approves new IT policy", "https://a.in/2"))
    assert a_added and b_added and a_cluster == b_cluster
    assert not dup_added  # same canonical URL
    assert other_added and other_cluster != a_cluster
    assert db.conn.execute("SELECT item_count FROM clusters WHERE cluster_id = ?", (a_cluster,)).fetchone()[0] == 2


def test_get_fresh_items_min_priority_drops_low_priority_feeds(tmp_path):
    path = tmp_path / "p.db"
    db = DB(path)
    loud, calm = _feed(id="P2F", name="Firehose", priority="P2"), _feed(id="P0F", name="Core", priority="P0")
    db.sync_feeds([loud, calm])
    c = Clusterer(db, now=NOW)
    c.add(_item("Company X files board meeting notice", "https://nse.in/x.pdf", loud))
    c.add(_item("Metro fares to rise from October", "https://core.in/m", calm))
    db.conn.commit()
    db.close()
    assert len(get_fresh_items(db_path=str(path))) == 2
    only_core = get_fresh_items(db_path=str(path), min_priority="P1")
    assert [s["title"] for s in only_core] == ["Metro fares to rise from October"]


def test_get_fresh_items_lists_every_source(tmp_path):
    path = tmp_path / "f.db"
    db = DB(path)
    c = Clusterer(db, now=NOW)
    c.add(_item("RBI keeps repo rate unchanged at 5.5%", "https://a.in/rbi"))
    c.add(
        _item(
            "RBI keeps repo rate unchanged at 5.5 %",
            "https://b.in/rbi",
            _feed(id="T2", name="Other", subject_area="Stock Markets"),
        )
    )
    c.add(_item("Old story", "https://a.in/old", published=NOW - timedelta(hours=72)))
    db.conn.commit()
    db.close()
    fresh = get_fresh_items(hours=24, db_path=str(path))
    assert len(fresh) == 1
    assert fresh[0]["source_count"] == 2
    assert {s["source_name"] for s in fresh[0]["sources"]} == {"Test Paper", "Other"}
    # the area filter picks which stories come back, but a story always reports every outlet
    # covering it (the shortlist's outlet score depends on that)
    in_markets = get_fresh_items(subject_area="Stock Markets", db_path=str(path))
    assert len(in_markets) == 1 and in_markets[0]["source_count"] == 2
    assert get_fresh_items(subject_area="Politics", db_path=str(path)) == []


# ------------------------------------------------------------------ polling with conditional requests


def test_poller_uses_etag_and_handles_304(tmp_path):
    feed_body = rss(
        [
            {
                "title": "Water tanker prices double",
                "link": "https://a.in/w?utm_source=rss",
                "date": rfc822(NOW),
                "summary": "s",
            }
        ]
    )
    seen = []

    def handler(request):
        if request.url.path == "/robots.txt":  # read once per host per run before any feed fetch
            return httpx.Response(404)
        seen.append(dict(request.headers))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=feed_body, headers={"etag": '"v1"', "content-type": "application/rss+xml"})

    settings = cfg.Settings(feeds=[_feed(poll_interval_minutes=15)])
    db = DB(tmp_path / "p.db")

    async def go():
        async with PoliteClient(transport=httpx.MockTransport(handler), retries=0) as client:
            poller = Poller(settings, db, client)
            first = await poller.poll_once()
            second = await poller.poll_once(force=True)
            return first, second

    first, second = asyncio.run(go())
    assert first["added"] == 1 and second["not_modified"] == 1 and second["added"] == 0
    assert first["polled"] == 1 and first["fetched"] == 1  # each feed counted once
    assert seen[1]["if-none-match"] == '"v1"'
    row = db.conn.execute("SELECT url, canonical_url, summary FROM items").fetchone()
    assert row["canonical_url"] == "https://a.in/w" and row["summary"] == "s"
    state = db.feed_state("T1")
    assert state["etag"] == '"v1"' and state["next_poll_utc"] > iso(NOW)


def test_poller_skips_robots_disallowed_feeds_when_respecting_robots(tmp_path):
    settings = cfg.Settings(feeds=[_feed(robots_allowed="no")], respect_robots=True)
    poller = Poller(settings, DB(tmp_path / "r.db"), client=None)
    assert poller.due(NOW) == []
    settings.robots_exceptions = ["a.in"]
    assert len(poller.due(NOW)) == 1  # the team chose to poll this domain anyway
    settings.robots_exceptions, settings.respect_robots = [], False
    assert len(poller.due(NOW)) == 1


# ------------------------------------------------------------------ health check


def test_health_alerts_p0_but_never_switches_a_feed_by_itself(tmp_path, monkeypatch):
    # 29 Sep 2026: the automatic repair could land on Google News or a forbidden page; health now
    # only alerts, and a person picks any replacement
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(404)

    async def no_repair(*a, **k):
        raise AssertionError("health must not run rediscovery")

    monkeypatch.setattr("feedkit.discover.repair", no_repair)
    settings = cfg.Settings(feeds=[_feed(priority="P0"), _feed(id="T2", name="Minor", priority="P2")])
    db = DB(tmp_path / "h.db")
    log = tmp_path / "health.log"

    async def go():
        async with PoliteClient(transport=httpx.MockTransport(handler), retries=0) as client:
            return await health_mod.run_health(settings, db, client, log_path=log, verified_csv=tmp_path / "none.csv")

    asyncio.run(go())
    text = log.read_text(encoding="utf-8")
    assert "ALERT P0 T1" in text and "REPAIRED" not in text and "Minor" not in text  # P2 feeds never alert
    assert db.feed_state("T1")["active_url"] == settings.feeds[0].url  # never switched
    assert db.feed_state("T2")["status"] == "BROKEN"
    assert set(hosts) == {"a.in"}  # only the feed's own site (and its robots.txt), never Google News


def test_config_loads_yaml(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "defaults": {"respect_robots": False},
                "feeds": [
                    {"id": "A1", "name": "X", "url": "https://a.in/feed", "subject_area": "Politics", "unknown_key": 1}
                ],
            }
        )
    )
    settings = cfg.load(path)
    assert settings.respect_robots is False and settings.feeds[0].id == "A1"


def test_reddit_listing_parse():
    payload = {
        "data": {
            "children": [
                {
                    "data": {
                        "subreddit": "bangalore",
                        "title": "Rent hike",
                        "permalink": "/r/bangalore/x",
                        "score": 120,
                        "num_comments": 45,
                        "created_utc": 1790000000,
                    }
                }
            ]
        }
    }
    post = parse_listing(payload)[0]
    assert post["score"] == 120 and post["url"] == "https://www.reddit.com/r/bangalore/x"
