import asyncio
from datetime import datetime, timezone

import httpx

from riffi_ingest.fetchers import feeds
from riffi_ingest.fetchers.feeds import Validators, fetch_source, fetch_sources, plan
from riffi_ingest.sources import Source, load_sources
from tests.feedtools import fake_client, rfc822, rss

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
FEED = rss([{"title": "Story", "link": "https://a.in/1", "date": rfc822(NOW)}], feed_title="A")


def src(route, fetch_url="https://a.in/feed", **kw):
    return Source(
        source_id=kw.pop("source_id", "S1"), name="n", category="c", route_type=route, fetch_url=fetch_url, **kw
    )


def run(coro):
    return asyncio.run(coro)


def test_plan_for_each_route_type():
    assert plan(src(feeds.NATIVE)) == ("https://a.in/feed", False, "")
    assert plan(src(feeds.GOOGLE_NEWS, "https://news.google.com/rss/search?q=x"))[0].startswith("https://news.google")
    assert plan(src(feeds.MANUAL, ""))[2] == "manual source"
    assert plan(src(feeds.PAGE_MONITOR, "https://gov.in/page")) == ("https://gov.in/page", False, "")
    assert "not a URL" in plan(src(feeds.PAGE_MONITOR, "Monitor holiday notification page"))[2]  # S047


def test_x_rows_never_touch_x_and_run_on_their_backup():
    x = src(feeds.X_INSTAGRAM, "Create in RSS.app", backup_google_news_url="https://news.google.com/rss/search?q=DKS")
    assert plan(x) == ("https://news.google.com/rss/search?q=DKS", True, "")
    url, on_backup, reason = plan(src(feeds.X_INSTAGRAM, "Create in RSS.app"))  # S108: no backup
    assert url == "" and on_backup and "no backup" in reason


def test_rows_with_an_instruction_instead_of_a_url_are_skipped():
    assert "needs a YouTube channel ID" in plan(src(feeds.YOUTUBE, "Needs channel ID"))[2]  # S121
    pib = "Pick PIB Bengaluru on https://www.pib.gov.in/ViewRss.aspx?reg=1&lang=1"  # S107
    assert "not a URL" in plan(src(feeds.NATIVE, pib))[2]


def test_telegram_reads_the_public_channel_page_unless_an_rsshub_is_set():
    tg = src(feeds.TELEGRAM, "https://rsshub.app/telegram/channel/Prajavani1947")
    assert plan(tg)[0] == "https://t.me/s/Prajavani1947"
    assert plan(src(feeds.TELEGRAM, "https://t.me/tv9kannadaofficial/"))[0] == "https://t.me/s/tv9kannadaofficial"
    assert "no Telegram channel name" in plan(src(feeds.TELEGRAM, "https://t.me/s/"))[2]
    assert plan(tg, "https://rss.example.in/hub/")[0] == "https://rss.example.in/hub/telegram/channel/Prajavani1947"


TG_PAGE = """<!DOCTYPE html><html><head><meta property="og:title" content="Prajavani"></head><body>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="Prajavani1947/10">
  <div class="tgme_widget_message_bubble">
    <div class="tgme_widget_message_text js-message_text" dir="auto">BBMP floats tunnel road tender.<br/>Bids due Nov 3</div>
    <div class="tgme_widget_message_footer"><a class="tgme_widget_message_date" href="https://t.me/Prajavani1947/10">
      <time datetime="2026-10-04T05:30:00+00:00" class="time">11:00</time></a></div>
  </div></div></div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="Prajavani1947/11">
  <a class="tgme_widget_message_photo_wrap" href="https://t.me/Prajavani1947/11"></a>
  <a class="tgme_widget_message_date" href="https://t.me/Prajavani1947/11"><time datetime="2026-10-04T05:40:00+00:00">
  </time></a></div></div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="Prajavani1947/12">
  <div class="tgme_widget_message_reply"><div class="tgme_widget_message_text js-message_reply_text">quoted old post</div></div>
  <div class="tgme_widget_message_text js-message_text">ಮೆಟ್ರೋ ದರ <b>ಏರಿಕೆ</b> &amp; more</div>
  <a class="tgme_widget_message_date" href="https://t.me/Prajavani1947/12"><time datetime="2026-10-04T05:50:00+00:00">
  </time></a></div></div>
</body></html>"""


def test_a_telegram_page_becomes_entries_newest_first():
    tg = src(feeds.TELEGRAM, "https://rsshub.app/telegram/channel/Prajavani1947")
    seen = {}

    def handler(request):
        seen["url"], seen["accept"] = str(request.url), request.headers["accept"]
        return httpx.Response(200, text=TG_PAGE, headers={"content-type": "text/html"})

    async def go():
        async with fake_client(handler) as client:
            return await fetch_source(client, tg)

    out = run(go())
    assert seen["url"] == "https://t.me/s/Prajavani1947" and "text/html" in seen["accept"]
    assert out.status == "ok" and out.feed_title == "Prajavani"
    assert [e.link for e in out.entries] == ["https://t.me/Prajavani1947/12", "https://t.me/Prajavani1947/10"]
    assert out.entries[0].summary == "ಮೆಟ್ರೋ ದರ ಏರಿಕೆ & more"  # the quoted reply is not this post's text
    assert out.entries[1].summary == "BBMP floats tunnel road tender.\nBids due Nov 3"
    assert out.entries[1].published == datetime(2026, 10, 4, 5, 30, tzinfo=timezone.utc)
    assert out.entries[1].guid == "Prajavani1947/10" and out.entries[1].title == ""


def test_a_telegram_page_with_no_posts_or_an_error_is_a_failure():
    tg = src(feeds.TELEGRAM, "https://t.me/someone")

    def fetch(handler):
        async def go():
            async with fake_client(handler) as client:
                return await fetch_source(client, tg)

        return run(go())

    empty = fetch(lambda r: httpx.Response(200, text="<html><body>Contact @someone</body></html>"))
    assert empty.status == "error" and "no posts" in empty.reason
    gone = fetch(lambda r: httpx.Response(404))
    assert gone.status == "error" and gone.http_status == 404


def test_fetch_ok_keeps_entries_and_validators():
    def handler(request):
        return httpx.Response(200, content=FEED, headers={"ETag": '"e1"', "content-type": "application/rss+xml"})

    async def go():
        async with fake_client(handler) as client:
            return await fetch_source(client, src(feeds.NATIVE))

    out = run(go())
    assert out.status == "ok" and out.feed_title == "A" and out.entries[0].title == "Story"
    assert out.validators.etag == '"e1"' and out.http_status == 200


def test_fetch_not_modified_keeps_the_old_validators():
    async def go():
        async with fake_client(lambda r: httpx.Response(304)) as client:
            return await fetch_source(client, src(feeds.NATIVE), validators=Validators(etag='"e1"'))

    out = run(go())
    assert out.status == "not_modified" and out.entries == [] and out.validators.etag == '"e1"'


def test_html_instead_of_a_feed_is_an_error():
    def handler(request):
        return httpx.Response(
            200, text="<html><body>Access denied</body></html>", headers={"content-type": "text/html"}
        )

    async def go():
        async with fake_client(handler) as client:
            return await fetch_source(client, src(feeds.NATIVE))

    out = run(go())
    assert out.status == "error" and "HTML page" in out.reason


def test_one_bad_source_does_not_stop_the_others(monkeypatch):
    def handler(request):
        if request.url.host == "down.in":
            return httpx.Response(500)
        if request.url.host == "odd.in":
            return httpx.Response(200, content=FEED)
        return httpx.Response(200, content=FEED)

    def broken_parser(content):
        raise ValueError("parser bug")

    sources = [
        src(feeds.NATIVE, "https://down.in/feed", source_id="S1"),
        src(feeds.MANUAL, "", source_id="S2"),
        src(feeds.NATIVE, "https://ok.in/feed", source_id="S3"),
    ]

    async def go():
        async with fake_client(handler) as client:
            first = await fetch_sources(client, sources)
            monkeypatch.setattr(feeds, "parse_feed", broken_parser)
            second = await fetch_source(client, src(feeds.NATIVE, "https://odd.in/feed"))
            return first, second

    outcomes, crashed = run(go())
    assert [o.status for o in outcomes] == ["error", "skipped", "ok"]
    assert outcomes[0].reason == "HTTP 500"
    assert crashed.status == "error" and "parser bug" in crashed.reason


def test_every_row_in_feeds_csv_gets_a_plan(repo_root):
    sources = load_sources(repo_root / "feeds.csv")
    assert len(sources) == 131
    plans = {s.source_id: plan(s) for s in sources}
    fetchable = [sid for sid, (url, _, _) in plans.items() if url]
    on_backup = [sid for sid, (url, backup, _) in plans.items() if url and backup]
    assert len(on_backup) == 25  # every X/Instagram row has a backup since S108 got one (5 Oct 2026)
    assert (
        sum(1 for s in sources if s.route_type == feeds.PAGE_MONITOR and plans[s.source_id][0]) == 17
    )  # S007, S015, S041 moved to Google News (D-013), then S008 and S009 (D-015)
    assert [sid for sid, (url, _, _) in plans.items() if not url] == ["S112"]  # only the manual source
    assert all(plans[sid][0].startswith("https://") for sid in fetchable)
    assert sum(s.priority_x_feed for s in sources) == 5


def test_an_x_row_whose_backup_is_not_google_news_is_skipped():
    x = src(feeds.X_INSTAGRAM, "Create in RSS.app", backup_google_news_url="https://x.com/someone")
    url, on_backup, reason = plan(x)
    assert url == "" and on_backup and "not a Google News feed" in reason


def test_many_sources_on_one_site_never_time_out_waiting_their_turn():
    # the reviewer's first-run scenario, scaled down: 30 feeds on one site, 0.05 s between requests,
    # a 0.3 s limit per request. Queued all at once, most would time out waiting; one at a time, none do.
    feed = rss([{"title": "Story", "link": "https://a.in/1", "date": rfc822(NOW)}])
    gn = [src(feeds.GOOGLE_NEWS, f"https://news.google.com/rss/search?q=t{i}", source_id=f"G{i}") for i in range(30)]

    async def go():
        client = fake_client(lambda r: httpx.Response(200, content=feed), interval=(0.05, 0.05))
        client.deadline = 0.3
        async with client:
            return await fetch_sources(client, gn)

    outcomes = run(go())
    assert [o.status for o in outcomes] == ["ok"] * 30
    assert [o.source_id for o in outcomes] == [f"G{i}" for i in range(30)]  # input order kept
