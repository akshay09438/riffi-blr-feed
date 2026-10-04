import asyncio
from datetime import datetime, timezone

import httpx

from riffi_ingest.fetchers import feeds, pagemonitor
from riffi_ingest.fetchers.feeds import fetch_source, fetch_sources
from riffi_ingest.fetchers.outcome import Validators
from riffi_ingest.fetchers.pagemonitor import change_summary, is_stamp_line, page_lines, page_title
from riffi_ingest.sources import Source
from tests.feedtools import fake_client

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
SOURCE = Source("S025", "BMRCL", "Transport", feeds.PAGE_MONITOR, "https://english.bmrc.co.in/")


def page(body: str, stamp: str = "Last updated: 04/10/2026 10:32 AM", visitors: str = "120934") -> str:
    # the stamp lines sit inside the main content on purpose, so trafilatura keeps them and only
    # is_stamp_line can stop them from counting as a change
    return (
        "<html><head><title>BMRCL | Namma Metro</title></head><body>"
        f"<main><h1>Notices</h1>{body}<p>{stamp}</p><p>Visitors: {visitors}</p></main></body></html>"
    )


V1 = page("<p>Purple line services start at 5 AM from 10 October 2026.</p><p>Minimum fare is Rs 10.</p>")


def run(coro):
    return asyncio.run(coro)


def visit(html_text, previous=None, status=200, content_type="text/html; charset=utf-8", validators=None, seen=None):
    def handler(request):
        if seen is not None:
            seen.update(request.headers)
        return httpx.Response(status, text=html_text, headers={"content-type": content_type})

    async def go():
        async with fake_client(handler) as client:
            return await fetch_source(client, SOURCE, snapshot=previous, validators=validators)

    return run(go())


def test_stamp_lines_are_recognised():
    for stamp in (
        "Last updated: 04/10/2026 10:32 AM",
        "Last Updated On : 4th Oct 2026",
        "Last Updated : 04-Oct-2026",
        "Last Reviewed and Updated on : 04 Oct 2026",  # the NIC footer on Karnataka government sites
        "Last updated: Saturday, 4 October 2026",
        "Updated 5 minutes ago",
        "Updated: 04.10.2026 | 10.32 hrs",
        "Visitors: 120934",
        "Total Visits: 12345",
        "Page Views: 999",
        "Sunrise 06:10 Sunset 18:05",
        "Copyright 2026 All rights reserved",
    ):
        assert is_stamp_line(stamp), stamp


def test_content_is_never_a_stamp():
    for line in (
        "Purple line services start at 5 AM from 10 October 2026.",
        "Minimum fare is Rs 10.",
        "Fares updated from 10 October 2026 for all lines",  # "updated" plus real words is news
        "Date: 15/10/2026",
        "Time: 10:30 AM",
        "1 | 23/10/2026",
        "04-10-2026 | 32.1 | 21.4",
        "| 12 | 34 | 56 |",
        "ಅಕ್ಟೋಬರ್ 10 ರಿಂದ ರಜೆ 10/10/2026",  # Kannada: a holiday notice, not a stamp
    ):
        assert not is_stamp_line(line), line


def test_page_text_keeps_content_and_drops_stamps():
    lines = page_lines(V1)
    assert "Minimum fare is Rs 10." in lines
    assert not any("Last updated" in line or "Visitors" in line for line in lines)
    assert page_title(V1) == "BMRCL | Namma Metro"


def test_a_bare_date_right_after_a_stamp_label_is_dropped_too():
    lines = page_lines(page("<p>Minimum fare is Rs 10.</p><p>Last updated:</p><p>04/10/2026</p>"))
    assert "04/10/2026" not in lines and "Minimum fare is Rs 10." in lines


def test_page_title_ignores_an_svg_title():
    assert page_title("<html><body><svg><title>icon</title></svg></body></html>") == ""


def test_first_visit_only_takes_a_snapshot():
    out = visit(V1)
    assert out.status == "ok" and out.entries == []
    assert out.snapshot and out.snapshot.title == "BMRCL | Namma Metro" and len(out.snapshot.text_hash) == 64


def test_a_new_stamp_or_counter_or_whitespace_is_not_a_change():
    first = visit(V1).snapshot
    again = page(
        "<p>Purple line services   start at 5 AM from 10 October 2026.</p>\n\n<p>Minimum fare is Rs 10.</p>",
        stamp="Last updated: 05/10/2026 09:01 AM",
        visitors="121500",
    )
    out = visit(again, previous=first)
    assert out.status == "ok" and out.entries == [] and out.snapshot.text_hash == first.text_hash


def test_lines_only_moving_around_is_not_a_change():
    first = visit(V1).snapshot
    swapped = page("<p>Minimum fare is Rs 10.</p><p>Purple line services start at 5 AM from 10 October 2026.</p>")
    assert visit(swapped, previous=first).entries == []


def test_a_real_change_emits_one_updated_item_with_the_diff():
    first = visit(V1).snapshot
    v2 = page("<p>Purple line services start at 5 AM from 15 October 2026.</p><p>Minimum fare is Rs 10.</p>")
    out = visit(v2, previous=first)
    (item,) = out.entries
    assert item.title == "BMRCL | Namma Metro updated" and item.link == "https://english.bmrc.co.in/"
    assert "Added: Purple line services start at 5 AM from 15 October 2026." in item.summary
    assert "Removed: Purple line services start at 5 AM from 10 October 2026." in item.summary
    assert "Minimum fare" not in item.summary
    assert item.published is not None and item.guid == f"S025:{first.text_hash[:12]}-{out.snapshot.text_hash[:12]}"


def test_a_change_in_a_table_of_numbers_is_reported():
    def table(n):
        return page(f"<table><tr><th>Ward</th><th>Seats</th></tr><tr><td>12</td><td>{n}</td></tr></table>")

    first = visit(table(34)).snapshot
    assert len(visit(table(35), previous=first).entries) == 1


def test_summary_is_capped():
    assert len(change_summary("", "\n".join(f"line {i} " * 20 for i in range(100)))) == 1000


def test_errors_keep_the_old_snapshot():
    first = visit(V1).snapshot
    down = visit("Service Unavailable", previous=first, status=503)
    assert down.status == "error" and down.reason == "HTTP 503" and down.snapshot == first
    pdf = visit("%PDF-1.7 ...", previous=first, content_type="application/pdf")
    assert pdf.status == "error" and "not a web page" in pdf.reason and pdf.snapshot == first


def test_a_bot_check_page_is_an_error_not_an_update():
    first = visit(V1).snapshot
    wall = "<html><head><title>Just a moment...</title></head><body><div id='cf-chl-widget'></div></body></html>"
    out = visit(wall, previous=first)
    assert out.status == "error" and "bot check" in out.reason
    assert out.entries == [] and out.snapshot == first


def test_an_extraction_crash_keeps_the_old_snapshot(monkeypatch):
    first = visit(V1).snapshot

    def boom(_content):
        raise ValueError("lxml choked")

    monkeypatch.setattr(pagemonitor, "_read_page", boom)
    out = visit(V1, previous=first)
    assert out.status == "error" and "lxml choked" in out.reason and out.snapshot == first


def test_slow_extraction_runs_off_the_event_loop_and_times_out(monkeypatch):
    import time

    first = visit(V1).snapshot
    monkeypatch.setattr(pagemonitor, "EXTRACT_TIMEOUT", 0.2)
    monkeypatch.setattr(pagemonitor, "_read_page", lambda content: time.sleep(1))
    other = Source("S1", "n", "c", feeds.NATIVE, "https://a.in/feed")
    feed = b'<?xml version="1.0"?><rss version="2.0"><channel><title>A</title></channel></rss>'

    def handler(request):
        if request.url.host == "a.in":
            return httpx.Response(200, content=feed)
        return httpx.Response(200, text=V1, headers={"content-type": "text/html"})

    async def go():
        async with fake_client(handler) as client:
            return await fetch_sources(client, [SOURCE, other], snapshots={"S025": first})

    page_out, feed_out = run(go())
    assert page_out.status == "error" and "extraction took over" in page_out.reason and page_out.snapshot == first
    assert feed_out.status == "ok"


def test_without_a_snapshot_no_validators_are_sent():
    seen = {}
    out = visit(V1, validators=Validators(etag='"old"'), seen=seen)
    assert "if-none-match" not in seen and out.status == "ok" and out.snapshot is not None


def test_pages_and_feeds_run_together():
    feed_src = Source("S1", "n", "c", feeds.NATIVE, "https://a.in/feed")
    feed = b'<?xml version="1.0"?><rss version="2.0"><channel><title>A</title></channel></rss>'

    def handler(request):
        if request.url.host == "a.in":
            return httpx.Response(200, content=feed)
        return httpx.Response(200, text=V1, headers={"content-type": "text/html"})

    async def go():
        async with fake_client(handler) as client:
            return await fetch_sources(client, [feed_src, SOURCE])

    feed_out, page_out = run(go())
    assert feed_out.status == "ok" and page_out.status == "ok" and page_out.snapshot is not None
