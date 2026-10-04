"""Parse an RSS or Atom feed (publisher, Google News, RSSHub Telegram, YouTube) into one entry shape.

Adapted from the panel project's feedkit/parse.py (copied, not imported - D-006), with author and
image added. Mapping entries onto the stored items shape (IST dates, language, publisher) is
step 2 (normalise), not this module.
"""

from __future__ import annotations

import calendar
import html
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)
IST = timezone(timedelta(hours=5, minutes=30))
SUMMARY_CHARS = 1000


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", text))).strip()


def looks_like_html(content: bytes, content_type: str = "") -> bool:
    """Some feeds answer 200 with an HTML page (a login wall, a moved site). Check before parsing."""
    head = content[:3000].lstrip().lower()
    if head.startswith((b"<?xml", b"<rss", b"<feed", b"<rdf")):
        return False
    return "html" in content_type.lower() or head.startswith(b"<!doctype html") or b"<html" in head


# Formats Indian government feeds use that feedparser cannot read. Dates without a timezone are
# taken as IST: every source that emits them (RBI, SEBI, PIB) is Indian.
FALLBACK_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S",
    "%a, %d %b %Y %H:%M",
    "%d %b, %Y %z",
    "%d %b, %Y",
    "%d %b %Y %H:%M:%S %z",
    "%d %b %Y %H:%M:%S",
    "%d %b %Y",
    "%d %B %Y",
    "%d %B, %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d-%b-%Y",
    "%d-%b-%Y %H:%M:%S",  # BSE / NSE: 27-Sep-2026 01:32:50
    "%d-%b-%Y %H:%M",
    "%d-%m-%Y %H:%M:%S",  # RBI Hindi feeds
    "%d %b %Y %I:%M%p",
)


def parse_loose_date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = WS_RE.sub(" ", text.strip())
    for fmt in FALLBACK_FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=IST).astimezone(timezone.utc)
    return None


def _struct_to_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    except (OverflowError, ValueError, TypeError):
        return None


@dataclass
class Entry:
    title: str = ""
    link: str = ""
    published: datetime | None = None  # UTC; None when the feed gives no readable date
    summary: str = ""
    author: str = ""
    image_url: str = ""
    source_title: str = ""  # Google News <source>: the real publisher
    source_url: str = ""
    guid: str = ""


@dataclass
class ParsedFeed:
    is_feed: bool = False
    kind: str = ""  # rss | atom | ""
    title: str = ""
    bozo_reason: str = ""  # feedparser's complaint about malformed XML, if any
    entries: list[Entry] = field(default_factory=list)


def _image(e) -> str:
    for m in e.get("media_content") or []:
        kind = m.get("medium") or m.get("type") or "image"  # untyped media is almost always a picture
        if m.get("url") and "image" in kind:
            return m["url"]
    for m in e.get("media_thumbnail") or []:
        if m.get("url"):
            return m["url"]
    for enc in e.get("enclosures") or []:
        if (enc.get("type") or "").startswith("image") and enc.get("href"):
            return enc["href"]
    for link in e.get("links") or []:
        if link.get("rel") == "enclosure" and (link.get("type") or "").startswith("image") and link.get("href"):
            return link["href"]
    m = IMG_RE.search(e.get("summary", "") or "")
    return m.group(1) if m else ""


def parse_feed(content: bytes) -> ParsedFeed:
    out = ParsedFeed()
    # always a stream: given raw bytes, feedparser first tries them as a file name, which on Windows
    # raised MemoryError for a large feed in the panel project
    d = feedparser.parse(io.BytesIO(content))
    version = d.get("version", "") or ""
    if d.get("bozo") and d.get("bozo_exception") is not None:
        out.bozo_reason = f"{type(d.bozo_exception).__name__}: {d.bozo_exception}"[:200]
    entries = d.get("entries", []) or []
    out.is_feed = bool(version) or bool(entries)
    if not out.is_feed:
        return out
    out.kind = "atom" if version.startswith("atom") else "rss"
    out.title = strip_html(d.feed.get("title", "")) if d.get("feed") else ""
    for e in entries:
        src = e.get("source") or {}
        out.entries.append(
            Entry(
                title=strip_html(e.get("title", "")),
                link=(e.get("link") or "").strip(),
                published=_struct_to_dt(e.get("published_parsed") or e.get("updated_parsed"))
                or parse_loose_date(e.get("published") or e.get("updated")),
                summary=strip_html(e.get("summary", ""))[:SUMMARY_CHARS],
                author=strip_html(e.get("author", "")),
                image_url=_image(e),
                source_title=strip_html(src.get("title", "")) if isinstance(src, dict) else "",
                source_url=(src.get("href", "") if isinstance(src, dict) else "") or "",
                guid=e.get("id", "") or "",
            )
        )
    return out
