"""Web page monitors (BRIEF.md step 1): for the 22 government, ticketing and data pages with no feed.

GET the page, pull out its main text with trafilatura, and compare it with the snapshot from last time.
When the text really changed, emit one entry: title = page title + " updated", link = the page,
published = when the change was seen, summary = what was added and removed.

What does not count as a change: whitespace, and "stamp" lines such as "Last updated: 04/10/2026
10:32 AM" or a visitor counter. Dates inside real content still count, so "effective from 10 October"
becoming "15 October" is reported.

The first visit to a page only records a snapshot (nothing to compare with yet). Storing snapshots
between runs is step 6 (page_snapshots table); this module takes the previous one in and hands the
new one back on the outcome.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import re
import time
from datetime import datetime, timezone

import trafilatura

from ..sources import Source
from .http import PAGE_ACCEPT, PoliteClient
from .outcome import FetchOutcome, PageSnapshot, Validators
from .parse import WS_RE, Entry

TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
DATE_TIME_RE = re.compile(
    r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"  # 04/10/2026, 4-10-26
    r"|\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b"  # 2026-10-04, 2026-10-04T10:32
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?|hrs|ist)?\b"  # 10:32, 10:32 AM
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*,?\s+\d{4}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    re.I,
)
# Words that make up a page-stamp line once its dates, times and numbers are removed.
STAMP_WORDS = {
    "last", "updated", "update", "modified", "reviewed", "on", "as", "at", "date", "time", "page", "site",
    "website", "visitors", "visitor", "count", "counter", "hits", "total", "today", "ist", "am", "pm", "of",
    "the", "content", "copyright", "all", "rights", "reserved", "by", "no",
}  # fmt: skip
WORD_RE = re.compile(r"[a-z]+")
SUMMARY_CHARS = 1000
MIN_LINE_CHARS = 3


def page_title(page_html: str) -> str:
    m = TITLE_RE.search(page_html)
    return WS_RE.sub(" ", html.unescape(m.group(1))).strip() if m else ""


def is_stamp_line(line: str) -> bool:
    """True for lines like "Last Updated On : 04/10/2026 10:32 AM", "Visitors: 12345" or a bare
    timestamp. A line of bare numbers (a table row: ward counts, box office) is content, not a stamp."""
    lowered = line.lower()
    rest = DATE_TIME_RE.sub(" ", lowered)
    words = WORD_RE.findall(re.sub(r"\d+", " ", rest))
    if words:
        return all(w in STAMP_WORDS for w in words)
    return rest != lowered  # no words at all: a stamp only if it held a date or time


def page_lines(page_html: str) -> list[str]:
    """The page's main text as comparable lines, without stamp lines."""
    text = trafilatura.extract(page_html, include_tables=True, include_comments=False, favor_recall=True)
    if not text:
        # pages that are mostly links and menus: fall back to all visible text, split into sentences
        text = "\n".join(SENTENCE_SPLIT_RE.split(trafilatura.html2txt(page_html) or ""))
    lines = (WS_RE.sub(" ", line).strip() for line in text.splitlines())
    return [line for line in lines if len(line) >= MIN_LINE_CHARS and not is_stamp_line(line)]


def snapshot_of(lines: list[str], title: str) -> PageSnapshot:
    text = "\n".join(lines)
    return PageSnapshot(hashlib.sha256(text.encode("utf-8")).hexdigest(), text, title)


def change_summary(old_text: str, new_text: str) -> str:
    old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
    added, removed = [], []
    for op in difflib.ndiff(old_lines, new_lines):
        if op.startswith("+ "):
            added.append(op[2:])
        elif op.startswith("- "):
            removed.append(op[2:])
    parts = []
    if added:
        parts.append("Added: " + " | ".join(added))
    if removed:
        parts.append("Removed: " + " | ".join(removed))
    summary = " || ".join(parts)
    return summary if len(summary) <= SUMMARY_CHARS else summary[: SUMMARY_CHARS - 1] + "…"


async def monitor_page(
    client: PoliteClient,
    source: Source,
    url: str,
    *,
    previous: PageSnapshot | None = None,
    validators: Validators | None = None,
    now: datetime | None = None,
) -> FetchOutcome:
    out = FetchOutcome(source.source_id, source.route_type, "error", url=url, snapshot=previous)
    started = time.monotonic()
    v = validators or Validators()
    res = await client.fetch(url, accept=PAGE_ACCEPT, etag=v.etag, last_modified=v.last_modified)
    out.http_status, out.error_kind = res.status, res.error_kind
    if res.not_modified:
        out.status, out.validators = "not_modified", v
    elif not res.ok:
        out.reason = res.error or f"HTTP {res.status}"
    elif "html" not in res.content_type.lower() and not res.content.lstrip()[:200].lower().startswith(
        (b"<!doctype", b"<html")
    ):
        out.reason = f"not a web page ({res.content_type or 'unknown type'})"
    else:
        page_html = res.text
        title = page_title(page_html)
        snap = snapshot_of(page_lines(page_html), title)
        out.status, out.snapshot, out.feed_title = "ok", snap, title
        out.validators = Validators(res.etag, res.last_modified)
        if previous is not None and snap.text_hash != previous.text_hash:
            out.entries = [
                Entry(
                    title=f"{title or source.name} updated",
                    link=url,
                    published=now or datetime.now(timezone.utc),
                    summary=change_summary(previous.text, snap.text),
                    guid=f"{source.source_id}:{snap.text_hash[:16]}",
                )
            ]
    out.elapsed = round(time.monotonic() - started, 2)
    return out
