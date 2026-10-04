import asyncio
from datetime import datetime, timezone

import httpx

from riffi_ingest.fetchers import feeds
from riffi_ingest.fetchers.feeds import fetch_source, fetch_sources
from riffi_ingest.fetchers.pagemonitor import change_summary, is_stamp_line, page_lines, page_title
from riffi_ingest.sources import Source
from tests.feedtools import fake_client

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
SOURCE = Source("S025", "BMRCL", "Transport", feeds.PAGE_MONITOR, "https://english.bmrc.co.in/")


def page(body: str, stamp: str = "Last updated: 04/10/2026 10:32 AM") -> str:
    return (
        "<html><head><title>BMRCL | Namma Metro</title></head><body>"
        "<nav><a href='/'>Home</a><a href='/about'>About us</a></nav>"
        f"<main><h1>Notices</h1>{body}</main><footer><p>{stamp}</p><p>Visitors: 120934</p></footer></body></html>"
    )


V1 = page("<p>Purple line services start at 5 AM from 10 October 2026.</p><p>Minimum fare is Rs 10.</p>")


def run(coro):
    return asyncio.run(coro)


def visit(html_text, previous=None, status=200, content_type="text/html; charset=utf-8"):
    def handler(request):
        return httpx.Response(status, text=html_text, headers={"content-type": content_type})

    async def go():
        async with fake_client(handler) as client:
            return await fetch_source(client, SOURCE, snapshot=previous)

    return run(go())


def test_stamp_lines_are_recognised():
    assert is_stamp_line("Last updated: 04/10/2026 10:32 AM")
    assert is_stamp_line("Last Updated On : 4th Oct 2026")
    assert is_stamp_line("Visitors: 120934")
    assert is_stamp_line("© 2026 All rights reserved")
    assert not is_stamp_line("Purple line services start at 5 AM from 10 October 2026.")
    assert not is_stamp_line("Minimum fare is Rs 10.")
    assert is_stamp_line("04/10/2026 10:32")  # a bare timestamp
    assert not is_stamp_line("| 12 | 34 | 56 |")  # a table row of numbers is content


def test_a_change_in_a_table_of_numbers_is_reported():
    def table(n):
        return page(f"<table><tr><th>Ward</th><th>Seats</th></tr><tr><td>12</td><td>{n}</td></tr></table>")

    first = visit(table(34)).snapshot
    out = visit(table(35), previous=first)
    assert len(out.entries) == 1


def test_page_text_keeps_content_and_drops_stamps():
    lines = page_lines(V1)
    assert "Minimum fare is Rs 10." in lines
    assert not any("Last updated" in line or "Visitors" in line for line in lines)
    assert page_title(V1) == "BMRCL | Namma Metro"


def test_first_visit_only_takes_a_snapshot():
    out = visit(V1)
    assert out.status == "ok" and out.entries == []
    assert out.snapshot and out.snapshot.title == "BMRCL | Namma Metro" and len(out.snapshot.text_hash) == 64


def test_a_new_stamp_or_counter_or_whitespace_is_not_a_change():
    first = visit(V1).snapshot
    again = page(
        "<p>Purple line services   start at 5 AM from 10 October 2026.</p>\n\n<p>Minimum fare is Rs 10.</p>",
        stamp="Last updated: 05/10/2026 09:01 AM",
    ).replace("120934", "121500")
    out = visit(again, previous=first)
    assert out.status == "ok" and out.entries == [] and out.snapshot.text_hash == first.text_hash


def test_a_real_change_emits_one_updated_item_with_the_diff():
    first = visit(V1).snapshot
    v2 = page("<p>Purple line services start at 5 AM from 15 October 2026.</p><p>Minimum fare is Rs 10.</p>")
    out = visit(v2, previous=first)
    (item,) = out.entries
    assert item.title == "BMRCL | Namma Metro updated" and item.link == "https://english.bmrc.co.in/"
    assert "Added: Purple line services start at 5 AM from 15 October 2026." in item.summary
    assert "Removed: Purple line services start at 5 AM from 10 October 2026." in item.summary
    assert item.published is not None and item.guid.startswith("S025:")
    assert out.snapshot.text_hash != first.text_hash


def test_summary_is_capped():
    assert len(change_summary("", "\n".join(f"line {i} " * 20 for i in range(100)))) == 1000


def test_errors_keep_the_old_snapshot():
    first = visit(V1).snapshot
    down = visit("Service Unavailable", previous=first, status=503)
    assert down.status == "error" and down.reason == "HTTP 503" and down.snapshot == first
    pdf = visit("%PDF-1.7 ...", previous=first, content_type="application/pdf")
    assert pdf.status == "error" and "not a web page" in pdf.reason and pdf.snapshot == first


def test_pages_and_feeds_run_together():
    feed_src = Source("S1", "n", "c", feeds.NATIVE, "https://a.in/feed")

    def handler(request):
        if request.url.host == "a.in":
            return httpx.Response(
                200, content=b'<?xml version="1.0"?><rss version="2.0"><channel><title>A</title></channel></rss>'
            )
        return httpx.Response(200, text=V1, headers={"content-type": "text/html"})

    async def go():
        async with fake_client(handler) as client:
            return await fetch_sources(client, [feed_src, SOURCE])

    feed_out, page_out = run(go())
    assert feed_out.status == "ok" and page_out.status == "ok" and page_out.snapshot is not None
