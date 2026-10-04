from datetime import datetime, timezone

from riffi_ingest.fetchers.parse import looks_like_html, parse_feed, parse_loose_date, strip_html
from tests.feedtools import rfc822, rss

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)


def test_rss_entries_with_author_and_image():
    content = rss(
        [
            {
                "title": "Metro <b>fare</b> hike",
                "link": " https://a.in/1 ",
                "date": rfc822(NOW),
                "summary": "<p>BMRCL &amp; fares</p>",
                "extra": '<author>desk@a.in (City Desk)</author><media:content url="https://a.in/1.jpg" medium="image"/>',
            },
            {
                "title": "Second",
                "link": "https://a.in/2",
                "date": rfc822(NOW),
                "summary": '<img src="https://a.in/2.png"> text',
            },
        ],
        feed_title="A News",
    )
    feed = parse_feed(content)
    assert feed.is_feed and feed.kind == "rss" and feed.title == "A News"
    first, second = feed.entries
    assert first.title == "Metro fare hike" and first.link == "https://a.in/1"
    assert first.summary == "BMRCL & fares" and first.published == NOW
    assert "City Desk" in first.author and first.image_url == "https://a.in/1.jpg"
    assert second.image_url == "https://a.in/2.png"  # falls back to the first <img> in the summary


def test_atom_youtube_feed():
    content = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:media="http://search.yahoo.com/mrss/"><title>Channel</title>
      <entry><id>yt:video:abc</id><title>Budget explained</title>
      <link rel="alternate" href="https://www.youtube.com/watch?v=abc"/>
      <author><name>Channel</name></author><published>2026-10-04T05:00:00+00:00</published>
      <media:group><media:thumbnail url="https://i.ytimg.com/vi/abc/hq.jpg"/></media:group></entry></feed>"""
    feed = parse_feed(content)
    assert feed.kind == "atom"
    (e,) = feed.entries
    assert e.link == "https://www.youtube.com/watch?v=abc" and e.guid == "yt:video:abc"
    assert e.author == "Channel" and e.image_url == "https://i.ytimg.com/vi/abc/hq.jpg"
    assert e.published == datetime(2026, 10, 4, 5, 0, tzinfo=timezone.utc)


def test_google_news_source_element_is_kept():
    content = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>GN</title><item>
      <title>Tunnel road tender - Deccan Herald</title><link>https://news.google.com/rss/articles/CBMiX?oc=5</link>
      <source url="https://www.deccanherald.com">Deccan Herald</source></item></channel></rss>"""
    (e,) = parse_feed(content).entries
    assert e.source_title == "Deccan Herald" and e.source_url == "https://www.deccanherald.com"
    assert e.published is None


def test_government_dates_without_timezone_are_ist():
    assert parse_loose_date("24 Sep, 2026 +0530") == datetime(2026, 9, 23, 18, 30, tzinfo=timezone.utc)
    assert parse_loose_date("Fri, 25 Sep 2026 21:50:00") == datetime(2026, 9, 25, 16, 20, tzinfo=timezone.utc)
    assert parse_loose_date("27-Sep-2026 01:32:50") == datetime(2026, 9, 26, 20, 2, 50, tzinfo=timezone.utc)
    assert parse_loose_date("not a date") is None


def test_html_page_is_not_a_feed():
    page = b"<!doctype html><html><body>Please log in</body></html>"
    assert looks_like_html(page, "text/html; charset=utf-8")
    assert not looks_like_html(rss([]), "text/html")  # some servers mislabel real feeds
    assert not parse_feed(b"just some text").is_feed


def test_strip_html():
    assert strip_html("<p>a&nbsp;<i>b</i></p>\n c") == "a b c"
    assert strip_html(None) == ""
