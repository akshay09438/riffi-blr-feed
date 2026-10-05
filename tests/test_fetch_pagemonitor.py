import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx

from riffi_ingest import health
from riffi_ingest.fetchers import feeds, pagemonitor
from riffi_ingest.fetchers.feeds import fetch_source, fetch_sources
from riffi_ingest.fetchers.outcome import PageSnapshot, Validators
from riffi_ingest.fetchers.pagemonitor import (
    change_summary,
    is_js_notice,
    is_policy_title,
    is_stamp_line,
    page_lines,
    page_title,
)
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


# --- site-template policy blocks and JavaScript shells (5 Oct 2026: S004, S109, S110, S048; S009, S008) ---

KA_TEMPLATE = (Path(__file__).parent / "fixtures" / "karnataka_gov_template.html").read_text(encoding="utf-8")
NEW_NOTICE = (
    '<li><a href="/press/2026-10-05-buses">BMTC adds 500 electric buses on Outer Ring Road from 10 October</a>'
    ' <span class="date">05/10/2026</span></li>'
)


def test_a_karnataka_template_page_keeps_its_notices_and_drops_the_hidden_policy_modal():
    text = "\n".join(page_lines(KA_TEMPLATE))
    assert "Cabinet approves new suburban rail land acquisition for Bengaluru" in text
    assert "ಟೆಂಡರ್ ಸಂಖ್ಯೆ 17" in text and "Media accreditation cards" in text
    for policy in ("ವೈಯಕ್ತಿಕ ಮಾಹಿತಿ", "personal information", "ಗೌಪ್ಯತಾ ನೀತಿ", "statement of law", "Disclaimer"):
        assert policy not in text, policy


def test_on_a_template_page_a_new_notice_is_a_change_and_a_policy_edit_is_not():
    first = visit(KA_TEMPLATE).snapshot
    policy_edit = KA_TEMPLATE.replace("<!--POLICY-->", " ಈ ನೀತಿಯನ್ನು 05/10/2026 ರಂದು ಪರಿಷ್ಕರಿಸಲಾಗಿದೆ.")
    assert visit(policy_edit, previous=first).entries == []
    out = visit(KA_TEMPLATE.replace("<!--NOTICE-->", NEW_NOTICE), previous=first)
    (item,) = out.entries
    assert "Added: - BMTC adds 500 electric buses on Outer Ring Road from 10 October 05/10/2026" in item.summary
    assert "Removed" not in item.summary


def test_policy_blocks_named_by_id_or_class_or_printed_under_a_heading_are_dropped():
    lines = page_lines(
        page(
            "<p>Water supply suspended in Jayanagar on 7 October 2026.</p>"
            "<div id='privacy-policy'><p>We collect your IP address and browser type for statistics.</p></div>"
            "<section class='terms-of-use'><p>Use of this website is governed by Indian law.</p></section>"
            "<h2>Hyperlinking Policy</h2><p>Prior permission is required to link to this site.</p>"
            "<p>Links to other websites are provided for convenience.</p>"
            "<h2>Tenders</h2><p>Tender 42: pipeline repair in Ward 151.</p>"
        )
    )
    text = "\n".join(lines)
    assert "Water supply suspended in Jayanagar on 7 October 2026." in lines and "Tender 42" in text
    for policy in ("IP address", "Indian law", "Prior permission", "convenience", "Hyperlinking"):
        assert policy not in text, policy


def test_policy_headings_are_recognised_but_real_policy_news_is_not():
    for title in ("Privacy Policy", "ಗೌಪ್ಯತಾ ನೀತಿ", "Disclaimer", "Terms & Conditions", "Hyperlinking Policy",
                  "Copyright Policy", "ಹಕ್ಕು ನಿರಾಕರಣೆ", "Terms of Use:"):  # fmt: skip
        assert is_policy_title(title), title
    for title in ("Policies", "Karnataka EV Policy 2025-30", "Terms and conditions for tender No 17", "Notices"):
        assert not is_policy_title(title), title
    lines = page_lines(page("<h2>Policies</h2><ul><li>Karnataka Startup Policy 2025-30 notified.</li></ul>"))
    assert "Karnataka Startup Policy 2025-30 notified." in "\n".join(lines)


JS_SHELL = (
    "<html><head><title>GBA</title></head><body><noscript>You need to enable JavaScript to run this app."
    "</noscript><div id='root'></div><script src='/static/js/main.js'></script></body></html>"
)


def test_a_javascript_app_shell_is_an_error_that_keeps_the_snapshot_and_gets_the_javascript_fix():
    first = visit(V1).snapshot
    out = visit(JS_SHELL, previous=first)
    assert out.status == "error" and "needs JavaScript" in out.reason
    assert out.entries == [] and out.snapshot == first
    fix = health.check(out, NOW).fix
    assert "needs JavaScript" in fix and "JSON endpoint" in fix
    assert visit(JS_SHELL).snapshot is None  # first visit: no snapshot from a shell


def test_a_javascript_notice_outside_noscript_is_not_content():
    shell = "<html><head><title>ECI</title></head><body><div id='app'><p>Please enable JavaScript to view this site.</p></div></body></html>"
    assert visit(shell).status == "error"
    assert is_js_notice("You need to enable JavaScript to run this app.")
    assert not is_js_notice("Workshop on JavaScript for government web developers on 12 October 2026 at Vidhana Soudha")
    assert "Minimum fare is Rs 10." in page_lines(
        page("<p>Minimum fare is Rs 10.</p><noscript>Enable JavaScript</noscript>")
    )


def test_a_blind_old_snapshot_is_replaced_without_an_updated_item():
    real = visit(KA_TEMPLATE).snapshot
    policy_only = PageSnapshot("old", "ಈ ಜಾಲತಾಣವು ನಿಮ್ಮಿಂದ ಯಾವುದೇ ವೈಯಕ್ತಿಕ ಮಾಹಿತಿಯನ್ನು ಸಂಗ್ರಹಿಸುವುದಿಲ್ಲ.\nPrivacy Policy", "t")
    shell_only = PageSnapshot("old", "You need to enable JavaScript to run this app.", "t")
    for blind in (policy_only, shell_only):
        out = visit(KA_TEMPLATE, previous=blind)
        assert out.status == "ok" and out.entries == [] and out.snapshot == real


def test_a_blind_snapshot_asks_for_the_full_page_so_a_304_cannot_keep_it():
    # S008/S009, 5 Oct: the server answered 304 to the conditional GET, so the stored JavaScript notice stayed
    for blind in (
        PageSnapshot("old", "You need to enable JavaScript to run this app.", "t"),
        PageSnapshot("old", "ಈ ಜಾಲತಾಣವು ನಿಮ್ಮಿಂದ ಯಾವುದೇ ವೈಯಕ್ತಿಕ ಮಾಹಿತಿಯನ್ನು ಸಂಗ್ರಹಿಸುವುದಿಲ್ಲ.\nಗೌಪ್ಯತೆ ನೀತಿಗಳು", "t"),
    ):
        seen = {}
        out = visit(V1, previous=blind, validators=Validators(etag='"old"', last_modified="x"), seen=seen)
        assert "if-none-match" not in seen and "if-modified-since" not in seen
        assert out.status == "ok" and out.entries == [] and "Minimum fare is Rs 10." in out.snapshot.text
    seen = {}
    visit(V1, previous=visit(V1).snapshot, validators=Validators(etag='"old"'), seen=seen)
    assert seen.get("if-none-match") == '"old"'  # a real snapshot still uses the conditional GET


# --- the real karnataka.gov.in pages (saved 5 Oct 2026): the news lists are the page ---

FIXTURES = Path(__file__).parent / "fixtures"
DIPR = (FIXTURES / "karnataka_gov_real_S004_dipr.html").read_text(encoding="utf-8")
BMTC = (FIXTURES / "karnataka_gov_real_S109_bmtc.html").read_text(encoding="utf-8")
CM_EN = (FIXTURES / "karnataka_gov_real_S004_cm_en.html").read_text(encoding="utf-8")
TEMPLATE_POLICY = ("ವೈಯಕ್ತಿಕ ಮಾಹಿತಿ", "personal information", "ನಿಯಮ ಮತ್ತು ಶರತ್ತುಗಳು", "ಸ್ಕ್ರೀನ್ ರೀಡರ್", "Screen Reader")


def assert_no_template_text(text):
    for policy in TEMPLATE_POLICY:
        assert policy not in text, policy
    assert " ago" not in text  # the relative ages change by themselves


def test_real_dipr_and_bmtc_pages_give_their_latest_news_not_the_hidden_policy_modals():
    dipr = page_lines(DIPR)
    assert "Land of sandalwood cinema invites global film makers" in dipr
    assert_no_template_text("\n".join(dipr))
    bmtc = page_lines(BMTC)
    assert "Student Pass" in bmtc  # "1: Student Pass 4 months ago" without its number and age
    assert "''ದಿವ್ಯ ದರ್ಶನʼʼ ಪ್ಯಾಕೇಜ್ ಪ್ರವಾಸದಡಿಯಲ್ಲಿ ಪರಿಚಯಿಸಲಾಗುತ್ತಿರುವ ನೂತನ ಮಾರ್ಗ" in bmtc  # Quick Announcements
    assert_no_template_text("\n".join(bmtc))


def test_real_cm_page_gives_its_visible_english_headlines_not_policy_or_gallery_captions():
    lines = page_lines(CM_EN)
    assert "No one can erase Gandhiji's name or ideology: Chief Minister D.K. Shivakumar" in lines
    assert any(line.startswith("Bengaluru, October 02, 2026:") for line in lines)
    text = "\n".join(lines)
    assert_no_template_text(text)
    assert "ಚಿತ್ರ ಸಂಪುಟ" not in text and "We collect no" not in text


def test_on_the_real_template_an_older_age_is_not_a_change_and_a_new_headline_is():
    first = visit(BMTC).snapshot
    aged = BMTC.replace("4 months ago", "5 months ago").replace("1 year ago", "2 years ago")
    assert aged != BMTC and visit(aged, previous=first).entries == []
    new_item = BMTC.replace(
        "Student Pass ",
        "Student Pass </a></p><p><a href='/52/kn'>BMTC adds 500 electric buses on ORR</a> 1 day ago</p><p><a>",
    )
    (item,) = visit(new_item, previous=first).entries
    assert "Added: BMTC adds 500 electric buses on ORR" in item.summary


def test_the_stored_policy_snapshot_of_a_real_page_is_replaced_without_an_updated_item():
    blind = PageSnapshot("eb00a106", "ಗೌಪ್ಯತೆ ನೀತಿಗಳು\nಈ ಜಾಲತಾಣವು ನಿಮ್ಮಿಂದ ಯಾವುದೇ ವೈಯಕ್ತಿಕ ಮಾಹಿತಿಯನ್ನು ಸಂಗ್ರಹಿಸುವುದಿಲ್ಲ.", "t")
    for real in (DIPR, BMTC, CM_EN):
        out = visit(real, previous=blind)
        assert out.status == "ok" and out.entries == [] and out.snapshot == visit(real).snapshot
