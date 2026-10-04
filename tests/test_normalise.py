import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from riffi_ingest.fetchers import gnews
from riffi_ingest.fetchers.outcome import FetchOutcome
from riffi_ingest.fetchers.parse import IST, Entry
from riffi_ingest.normalise import canonical_url, clean, clean_title, detect_language, norm_title
from riffi_ingest.safety.blocklist import Blocklist
from riffi_ingest.sources import Source
from tests.feedtools import NEW_STYLE, batch_answer, fake_client, new_style, old_style

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
SOURCES = {
    "S011": Source("S011", "Deccan Herald", "News", "Native publisher RSS/Atom", "https://dh.com/feed"),
    "S019": Source("S019", "GN tunnel road", "Civic", "Google News RSS", "https://news.google.com/rss/search?q=x"),
    "S001": Source("S001", "CM D.K. Shivakumar", "Politics", "X/Instagram via RSS.app", "Create in RSS.app"),
    "S101": Source("S101", "Prajavani Telegram", "News", "RSSHub Telegram", "https://rsshub.app/telegram/channel/p"),
}


def outcome(source_id, entries, status="ok", **kw):
    return FetchOutcome(source_id, SOURCES[source_id].route_type, status, entries=entries, **kw)


def entry(title="Tunnel road tender floated", link="https://www.deccanherald.com/city/tunnel-1", hours_ago=2, **kw):
    return Entry(title=title, link=link, published=NOW - timedelta(hours=hours_ago), **kw)


def run_clean(outcomes, blocklist=None, handler=None, resolver_kw=None):
    async def go():
        async with fake_client(handler or (lambda r: httpx.Response(404))) as client:
            resolver = gnews.Resolver(client, **(resolver_kw or {}))
            result = await clean(outcomes, SOURCES, blocklist=blocklist or Blocklist(), resolver=resolver, now=NOW)
            return result, resolver

    return asyncio.run(go())


def test_publisher_item_shape():
    e = entry(summary="BBMP floats the tender.", author="City Desk", image_url="https://dh.com/1.jpg", guid="g1")
    (item,) = run_clean([outcome("S011", [e])])[0].items
    assert item.title == "Tunnel road tender floated" and item.publisher == "Deccan Herald"
    assert item.url == "https://www.deccanherald.com/city/tunnel-1" and item.url_resolved
    assert item.published_at == NOW - timedelta(hours=2) and item.published_at.tzinfo == IST
    assert item.fetched_at == NOW and item.language == "en"
    assert (item.summary, item.author, item.image_url, item.raw_guid) == (
        "BBMP floats the tender.",
        "City Desk",
        "https://dh.com/1.jpg",
        "g1",
    )
    assert item.raw_payload["title"] == "Tunnel road tender floated" and len(item.item_id) == 40


def test_google_news_links_are_resolved_and_titles_cleaned():
    link = f"https://news.google.com/rss/articles/{old_style('https://www.thehindu.com/news/x.ece')}?oc=5"
    e = entry(
        title="Metro fare hike - The Hindu", link=link, source_title="The Hindu", source_url="https://www.thehindu.com"
    )
    (item,) = run_clean([outcome("S019", [e])])[0].items
    assert item.url == "https://www.thehindu.com/news/x.ece" and item.url_resolved
    assert item.title == "Metro fare hike" and item.publisher == "The Hindu"


def test_an_unresolved_google_news_link_is_kept_and_marked():
    e = entry(link=f"https://news.google.com/rss/articles/{NEW_STYLE}", source_title="DH")
    (item,) = run_clean([outcome("S019", [e])])[0].items  # the fake network answers 404
    assert item.url.startswith("https://news.google.com/") and not item.url_resolved


def test_x_backup_items_are_marked():
    (item,) = run_clean([outcome("S001", [entry()], on_backup=True)])[0].items
    assert item.on_backup


def test_old_items_are_dropped_and_never_spend_the_resolution_cap():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.startswith("/rss/articles/"):
            return httpx.Response(200, text='<div data-n-a-sg="S" data-n-a-ts="1"></div>')
        return httpx.Response(200, text=batch_answer("https://a.in/x"))

    old = entry(link=f"https://news.google.com/rss/articles/{new_style(b'old')}", hours_ago=24 * 8)
    fresh = entry(link=f"https://news.google.com/rss/articles/{new_style(b'new')}", hours_ago=1)
    result, resolver = run_clean([outcome("S019", [old, fresh])], handler=handler)
    assert [i.url for i in result.items] == ["https://a.in/x"]
    assert result.dropped == {"older than 7 days": 1}
    assert resolver.online_used == 1 and len(calls) == 2


def test_dates_missing_or_far_in_the_future_become_the_fetch_time():
    undated = Entry(title="a", link="https://a.in/1")
    future = Entry(title="b", link="https://a.in/2", published=NOW + timedelta(hours=7))
    soon = Entry(title="c", link="https://a.in/3", published=NOW + timedelta(hours=1))
    items = run_clean([outcome("S011", [undated, future, soon])])[0].items
    assert [i.published_at for i in items] == [NOW, NOW, NOW + timedelta(hours=1)]


def test_blocklisted_sites_are_dropped_by_link_by_google_news_source_and_by_resolved_url():
    bl = Blocklist()
    bl.add("bengalurumetro.in")
    direct = entry(link="https://www.bengalurumetro.in/status")
    via_source = entry(link=f"https://news.google.com/rss/articles/{NEW_STYLE}", source_url="https://bengalurumetro.in")
    via_resolution = entry(link=f"https://news.google.com/rss/articles/{old_style('https://bengalurumetro.in/x')}")
    ok = entry(link="https://english.bmrc.co.in/notice")
    result, _ = run_clean([outcome("S011", [direct, ok]), outcome("S019", [via_source, via_resolution])], blocklist=bl)
    assert [i.url for i in result.items] == ["https://english.bmrc.co.in/notice"]
    assert result.dropped == {"blocklisted (bengalurumetro.in)": 3}


def test_failed_fetches_and_linkless_entries_are_skipped():
    result, _ = run_clean([outcome("S011", [entry()], status="error"), outcome("S011", [Entry(title="no link")])])
    assert result.items == [] and result.dropped == {"no link": 1}


def test_telegram_publisher_is_the_channel_and_kannada_is_detected():
    post = entry(title="", link="https://t.me/p/1", summary="ಬೆಂಗಳೂರು ಮೆಟ್ರೋ ದರ ಏರಿಕೆ. ಹೊಸ ದರ ಇಂದಿನಿಂದ")
    (item,) = run_clean([outcome("S101", [post], feed_title="Prajavani")])[0].items
    assert item.publisher == "Prajavani" and item.language == "kn"
    assert item.title == "ಬೆಂಗಳೂರು ಮೆಟ್ರೋ ದರ ಏರಿಕೆ"


def test_language_detection():
    assert detect_language("Metro fare hike in Bengaluru") == "en"
    assert detect_language("ಮೆಟ್ರೋ ದರ ಏರಿಕೆ BMRCL") == "kn"
    assert detect_language("मेट्रो किराया बढ़ा") == "other"
    assert detect_language("") == "en"


def test_canonical_url_and_titles():
    assert canonical_url("http://amp.dh.com/amp/city/x/?utm_source=tw&id=2&fbclid=z") == "https://dh.com/city/x?id=2"
    assert canonical_url("https://m.timesofindia.com/a/amp_articleshow/1.cms") == (
        "https://timesofindia.indiatimes.com/a/articleshow/1.cms"
    )
    assert clean_title("Fare hike - The Hindu", "The Hindu") == "Fare hike"
    assert clean_title("A - B", "") == "A - B"
    assert norm_title("Metro: fare HIKE!") == "metro fare hike"
