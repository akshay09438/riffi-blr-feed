"""Parse RSS/Atom feeds and news sitemaps into one shape, and measure freshness."""

from __future__ import annotations

import calendar
import html
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser
from feedparser.datetimes import _parse_date as _feedparser_date

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
FUTURE_TOLERANCE = timedelta(hours=6)
FULL_TEXT_CHARS = 600


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def strip_html(text: str | None) -> str:
    if not text:
        return ""
    return WS_RE.sub(" ", html.unescape(TAG_RE.sub(" ", text))).strip()


def looks_like_html(content: bytes, content_type: str = "") -> bool:
    head = content[:3000].lstrip().lower()
    if head.startswith(b"<?xml") or head.startswith(b"<rss") or head.startswith(b"<feed"):
        return False
    return "html" in content_type.lower() or head.startswith(b"<!doctype html") or b"<html" in head


@dataclass
class Entry:
    title: str = ""
    link: str = ""
    published: datetime | None = None
    summary: str = ""
    has_content: bool = False
    content_chars: int = 0
    source_title: str = ""  # Google News <source>
    source_url: str = ""
    categories: list[str] = field(default_factory=list)
    guid: str = ""


@dataclass
class FeedStats:
    is_feed: bool = False
    kind: str = ""  # rss | atom | sitemap | ""
    version: str = ""
    feed_title: str = ""
    bozo: bool = False
    bozo_reason: str = ""
    entries: list[Entry] = field(default_factory=list)
    child_sitemaps: list[str] = field(default_factory=list)

    # computed by compute()
    entries_count: int = 0
    dated_count: int = 0
    future_dated: int = 0
    newest: datetime | None = None
    oldest: datetime | None = None
    items_per_day: float | None = None
    share_24h: float | None = None
    pct_title: float = 0.0
    pct_link: float = 0.0
    pct_date: float = 0.0
    pct_summary: float = 0.0
    pct_content: float = 0.0
    has_full_text: bool = False

    def compute(self, now: datetime | None = None) -> "FeedStats":
        now = now or utcnow()
        n = len(self.entries)
        self.entries_count = n
        if not n:
            return self
        limit = now + FUTURE_TOLERANCE
        dates = [e.published for e in self.entries if e.published]
        self.future_dated = sum(1 for d in dates if d > limit)
        dates = [d for d in dates if d <= limit]
        self.dated_count = len(dates)
        if dates:
            self.newest, self.oldest = max(dates), min(dates)
            span_days = (self.newest - self.oldest).total_seconds() / 86400
            if len(dates) >= 2 and span_days > 0:
                self.items_per_day = round((len(dates) - 1) / span_days, 2)
            self.share_24h = round(sum(1 for d in dates if now - d <= timedelta(hours=24)) / n, 3)
        pct = lambda k: round(100.0 * k / n, 1)  # noqa: E731
        self.pct_title = pct(sum(1 for e in self.entries if e.title))
        self.pct_link = pct(sum(1 for e in self.entries if e.link))
        self.pct_date = pct(sum(1 for e in self.entries if e.published))
        self.pct_summary = pct(sum(1 for e in self.entries if e.summary))
        self.pct_content = pct(sum(1 for e in self.entries if e.has_content))
        full = sum(1 for e in self.entries if e.content_chars >= FULL_TEXT_CHARS)
        self.has_full_text = full * 2 >= n
        return self


IST = timezone(timedelta(hours=5, minutes=30))
# Formats Indian government feeds use that feedparser cannot read. Dates without a
# timezone are taken as IST: every source that emits them (RBI, SEBI, PIB) is Indian.
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


def parse_feed(content: bytes) -> FeedStats:
    stats = FeedStats()
    root = _xml_root_name(content)
    if root in ("urlset", "sitemapindex"):
        return parse_sitemap(content)
    # always a stream: given raw bytes, feedparser first tries them as a file name, which on
    # Windows raised MemoryError for a large feed during the 27 Sep health pass
    d = feedparser.parse(io.BytesIO(content))
    stats.version = d.get("version", "") or ""
    stats.bozo = bool(d.get("bozo"))
    if stats.bozo and d.get("bozo_exception") is not None:
        stats.bozo_reason = f"{type(d.bozo_exception).__name__}: {d.bozo_exception}"[:200]
    stats.feed_title = strip_html(d.feed.get("title", "")) if d.get("feed") else ""
    entries = d.get("entries", []) or []
    stats.is_feed = bool(stats.version) or (bool(entries) and root in ("rss", "feed", "rdf"))
    if not stats.is_feed:
        return stats
    stats.kind = "atom" if stats.version.startswith("atom") else "rss"
    for e in entries:
        content_text = ""
        if e.get("content"):
            content_text = strip_html(" ".join(c.get("value", "") for c in e["content"]))
        src = e.get("source") or {}
        stats.entries.append(
            Entry(
                title=strip_html(e.get("title", "")),
                link=(e.get("link") or "").strip(),
                published=_struct_to_dt(e.get("published_parsed") or e.get("updated_parsed"))
                or parse_loose_date(e.get("published") or e.get("updated")),
                summary=strip_html(e.get("summary", ""))[:1000],
                has_content=bool(content_text),
                content_chars=len(content_text),
                source_title=strip_html(src.get("title", "")) if isinstance(src, dict) else "",
                source_url=(src.get("href", "") if isinstance(src, dict) else "") or "",
                categories=[t.get("term", "") for t in e.get("tags", []) if t.get("term")],
                guid=e.get("id", "") or "",
            )
        )
    return stats


def _xml_root_name(content: bytes) -> str:
    head = content[:4000].decode("utf-8", errors="ignore")
    head = re.sub(r"<\?.*?\?>", "", head, flags=re.S)
    head = re.sub(r"<!--.*?-->", "", head, flags=re.S)
    m = re.search(r"<\s*([A-Za-z_][\w.\-]*:)?([A-Za-z_][\w.\-]*)", head)
    return m.group(2).lower() if m else ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text.replace("Z", "+00:00"), fmt)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return _struct_to_dt(_feedparser_date(text)) or parse_loose_date(text)


def parse_sitemap(content: bytes) -> FeedStats:
    stats = FeedStats()
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        stats.bozo, stats.bozo_reason = True, f"ParseError: {exc}"[:200]
        return stats
    name = _local(root.tag)
    if name == "sitemapindex":
        stats.kind = "sitemap"
        stats.version = "sitemapindex"
        stats.child_sitemaps = [(el.text or "").strip() for el in root.iter() if _local(el.tag) == "loc" and el.text]
        return stats
    if name != "urlset":
        return stats
    stats.is_feed, stats.kind, stats.version = True, "sitemap", "urlset"
    for url_el in root:
        if _local(url_el.tag) != "url":
            continue
        entry = Entry()
        lastmod = None
        for el in url_el.iter():
            tag = _local(el.tag)
            if tag == "loc" and not entry.link:
                entry.link = (el.text or "").strip()
            elif tag == "title":
                entry.title = strip_html(el.text)
            elif tag == "publication_date":
                entry.published = _parse_date(el.text)
            elif tag == "lastmod":
                lastmod = _parse_date(el.text)
            elif tag == "keywords" and el.text:
                entry.categories = [k.strip() for k in el.text.split(",") if k.strip()]
        entry.published = entry.published or lastmod
        stats.entries.append(entry)
    return stats
