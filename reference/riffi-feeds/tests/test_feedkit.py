import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
from conftest import rss

from feedkit import gnews
from feedkit.discover import section_share
from feedkit.net import PoliteClient, Robots, RobotsCache
from feedkit.parse import FeedStats, parse_feed, parse_loose_date
from feedkit.verify import classify, is_low_frequency, verify_url

NOW = datetime(2026, 9, 27, 0, 0, tzinfo=timezone.utc)


def rfc822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


# ------------------------------------------------------------------ robots.txt

GNEWS_ROBOTS = """User-agent: *
Disallow: /
Allow: /$
Allow: /topics/
Allow: /stories/

User-agent: ClaudeBot
Disallow: /
"""


def test_robots_google_news_rss_is_disallowed_but_topics_allowed():
    r = Robots.parse(GNEWS_ROBOTS)
    assert r.allowed("https://news.google.com/rss/search?q=x") is False
    assert r.allowed("https://news.google.com/topics/abc") is True
    assert r.allowed("https://news.google.com/") is True


def test_robots_longest_match_and_allow_wins_tie():
    r = Robots.parse("User-agent: *\nDisallow: /news\nAllow: /news/feed\nDisallow: /x\nAllow: /x\n")
    assert r.allowed("https://a.in/news/feed") is True
    assert r.allowed("https://a.in/news/story") is False
    assert r.allowed("https://a.in/x") is True


def test_robots_specific_group_beats_star_and_wildcards():
    r = Robots.parse("User-agent: *\nDisallow: /\n\nUser-agent: RiffiFeedBot\nDisallow: /*.pdf$\n")
    assert r.allowed("https://a.in/feed") is True
    assert r.allowed("https://a.in/doc.pdf") is False


def test_robots_sitemaps_collected():
    r = Robots.parse("Sitemap: https://a.in/news-sitemap.xml\nUser-agent: *\nDisallow:\n")
    assert r.sitemaps == ["https://a.in/news-sitemap.xml"]
    assert r.allowed("https://a.in/anything") is True


# ------------------------------------------------------------------ parsing


def test_parse_rss_stats():
    items = [
        {
            "title": f"Story {i}",
            "link": f"https://a.in/{i}",
            "date": rfc822(NOW - timedelta(hours=8 * i)),
            "summary": "s",
        }
        for i in range(5)
    ]
    stats = parse_feed(rss(items)).compute(NOW)
    assert stats.is_feed and stats.entries_count == 5
    assert stats.newest == NOW
    assert stats.items_per_day == 3.0  # 4 gaps over 32 hours
    assert stats.share_24h == 0.8  # the 32-hour-old item is outside the last day
    assert stats.pct_title == 100.0


def test_government_dates_parse_as_ist():
    # SEBI: "24 Sep, 2026 +0530"; RBI: no timezone at all (IST)
    assert parse_loose_date("24 Sep, 2026 +0530") == datetime(2026, 9, 23, 18, 30, tzinfo=timezone.utc)
    assert parse_loose_date("Fri, 25 Sep 2026 21:50:00") == datetime(2026, 9, 25, 16, 20, tzinfo=timezone.utc)
    assert parse_loose_date("26 Sep 2026 8:11PM") == datetime(2026, 9, 26, 14, 41, tzinfo=timezone.utc)
    assert parse_loose_date("27-Sep-2026 01:32:50") == datetime(2026, 9, 26, 20, 2, 50, tzinfo=timezone.utc)  # BSE/NSE


def test_parse_news_sitemap_and_index():
    urlset = b"""<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
      xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
      <url><loc>https://a.in/bengaluru/x</loc><news:news><news:title>Metro fare hike</news:title>
      <news:publication_date>2026-09-26T10:00:00+05:30</news:publication_date></news:news></url></urlset>"""
    stats = parse_feed(urlset).compute(NOW)
    assert stats.kind == "sitemap" and stats.entries_count == 1
    assert stats.entries[0].title == "Metro fare hike"
    assert stats.newest == datetime(2026, 9, 26, 4, 30, tzinfo=timezone.utc)
    index = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://a.in/news.xml</loc></sitemap></sitemapindex>"""
    idx = parse_feed(index)
    assert not idx.is_feed and idx.child_sitemaps == ["https://a.in/news.xml"]


def test_html_is_not_a_feed():
    assert not parse_feed(b"<!doctype html><html><body>hi</body></html>").is_feed


# ------------------------------------------------------------------ classification


def _stats(newest_days_ago, entries=3):
    s = FeedStats(is_feed=True)
    s.entries_count = entries
    s.newest = NOW - timedelta(days=newest_days_ago) if newest_days_ago is not None else None
    return s


def test_classify_rules():
    assert classify(_stats(1), NOW, False) == "WORKING"
    assert classify(_stats(5), NOW, False) == "LAGGING"
    assert classify(_stats(10), NOW, True) == "WORKING"  # low-frequency: 14 days
    assert classify(_stats(31), NOW, True) == "STALE"
    assert classify(_stats(None), NOW, False) == "UNDATED"
    assert classify(_stats(1, entries=0), NOW, False) == "EMPTY"
    assert classify(FeedStats(), NOW, False) == "NOT_A_FEED"


def test_low_frequency_sources():
    assert is_low_frequency("https://rbi.org.in/pressreleases_rss.xml")
    assert is_low_frequency("https://www.youtube.com/feeds/videos.xml?channel_id=UC1")
    assert not is_low_frequency("https://inc42.com/feed/")


# ------------------------------------------------------------------ google news


def test_gnews_search_url_and_detection():
    url = gnews.search_url('site:deccanherald.com "tunnel road" when:1d', "KN")
    assert url.startswith("https://news.google.com/rss/search?q=site:deccanherald.com+%22tunnel+road%22+when:1d")
    assert url.endswith("hl=kn&gl=IN&ceid=IN:kn")
    assert gnews.is_gnews(url)
    assert gnews.is_gnews_article("https://news.google.com/rss/articles/CBMiabc?oc=5")


def test_gnews_offline_decode_old_style_id():
    payload = b'\x08\x13"\x1ahttps://example.in/story-1\xd2\x01\x00'
    art_id = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    assert gnews.decode_offline(art_id) == "https://example.in/story-1"
    new_style = base64.urlsafe_b64encode(b'\x08\x13"AU_yqLxyz').decode().rstrip("=")
    assert gnews.decode_offline(new_style) is None


def test_gnews_batchexecute_response_parsing():
    inner = json.dumps(["garturlres", "https://www.thehindu.com/news/x.ece", 1])
    body = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"]]) + "\n\n"
    assert gnews._extract_batch_url(body) == "https://www.thehindu.com/news/x.ece"


# ------------------------------------------------------------------ verify_url against a fake network


def _run(coro):
    return asyncio.run(coro)


def _fake(handler):
    return PoliteClient(transport=httpx.MockTransport(handler), retries=0)


FRESH = rss([{"title": "A", "link": "https://a.in/1", "date": rfc822(datetime.now(timezone.utc)), "summary": "s"}])


def test_a_site_that_refuses_the_bot_is_not_asked_again_in_disguise():
    # founder, 29 Sep 2026: bot identity only; the old browser fallback is gone
    seen = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.append(request.headers["user-agent"])
        return httpx.Response(403)

    async def go():
        async with _fake(handler) as client:
            return await verify_url(client, RobotsCache(client), "https://a.in/feed")

    res = _run(go())
    assert res["status"] == "BROKEN" and res["needs_browser_ua"] is False
    assert res["bot_status"] == 403 and res["robots_allowed"] == "yes"
    assert len(seen) == 1 and seen[0].startswith("RiffiFeedBot")


def test_verify_html_and_404():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        if request.url.path == "/html":
            return httpx.Response(200, text="<html><body>home</body></html>", headers={"content-type": "text/html"})
        return httpx.Response(404)

    async def go():
        async with _fake(handler) as client:
            robots = RobotsCache(client)
            return await verify_url(client, robots, "https://a.in/html"), await verify_url(
                client, robots, "https://a.in/gone"
            )

    html, gone = _run(go())
    assert html["status"] == "NOT_A_FEED"
    assert gone["status"] == "BROKEN" and gone["http_status"] == 404


def test_section_share():
    res = {
        "samples": [
            {"link": "https://et.com/tech/startups/x", "title": "Funding"},
            {"link": "https://et.com/news/y", "title": "Budget"},
        ]
    }
    assert section_share(res, ["startups"]) == 0.5


def test_fetch_gives_up_on_a_slow_drip_server():
    """NSE answers bots one byte at a time; the overall deadline must still fire."""

    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(50):
                await asyncio.sleep(0.1)
                yield b"<"

    def handler(request):
        return httpx.Response(200, stream=Drip())

    async def go():
        async with PoliteClient(transport=httpx.MockTransport(handler), retries=0, deadline=0.3) as client:
            return await client.fetch("https://slow.example/feed")

    res = _run(go())
    assert res.error_kind == "timeout" and res.status is None
