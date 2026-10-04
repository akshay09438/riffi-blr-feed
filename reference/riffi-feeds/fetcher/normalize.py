"""Turn feed entries into the one item shape the agent reads, with clean URLs.

Only the title, a short summary and the link are kept. Full article bodies are never
stored: some publishers (e.g. Deccan Herald / Prajavani) allow headline and teaser
only, forbid commercial use and forbid archiving. Feeds are signals; link out.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from feedkit.parse import Entry, iso

TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "yclid",
    "twclid",
    "li_fat_id",
    "mc_cid",
    "mc_eid",
    "igshid",
    "igsh",
    "ref",
    "ref_src",
    "ref_url",
    "cmpid",
    "s_cid",
    "ito",
    "_ga",
    "_gl",
    "ncid",
    "ocid",
    "oc",
    "rss",
    "amp",
    "fromrss",
    "spm",
    "share",
    "smid",
    "sr_share",
}
TRACKING_VALUES = {"from": {"rss", "feed", "rssfeed"}, "outputtype": {"amp"}, "source": {"rss", "feed"}}
HOST_ALIASES = {
    "m.economictimes.com": "economictimes.indiatimes.com",
    "m.timesofindia.com": "timesofindia.indiatimes.com",
    "m.thewire.in": "thewire.in",
    "m.hindustantimes.com": "www.hindustantimes.com",
}
PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
WS_RE = re.compile(r"\s+")
FUTURE_TOLERANCE = timedelta(hours=6)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return url.strip()
    host = (parts.hostname or "").lower()
    host = HOST_ALIASES.get(host, host)
    if host.startswith("amp."):
        host = host[4:]
    netloc = host if not parts.port or parts.port in (80, 443) else f"{host}:{parts.port}"

    path = unquote(parts.path) if "%2f" in parts.path.lower() else parts.path
    path = re.sub(r"^/amp/story(?=/)", "", path)
    path = re.sub(r"^/amp(?=/)", "", path)
    path = path.replace("/amp_articleshow/", "/articleshow/").replace("/amp_news/", "/news/")
    path = re.sub(r"/amp/?$", "", path)
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")

    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        k = key.lower()
        if k.startswith("utm_") or k in TRACKING_PARAMS:
            continue
        if value.lower() in TRACKING_VALUES.get(k, ()):
            continue
        query.append((key, value))
    return urlunsplit(
        (
            "https" if parts.scheme in ("http", "https") else parts.scheme,
            netloc,
            path or "/",
            urlencode(sorted(query)),
            "",
        )
    )


def item_id(canonical: str) -> str:
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def clean_title(title: str, publisher: str = "") -> str:
    """Drop the ' - Publisher' suffix Google News appends."""
    title = WS_RE.sub(" ", title or "").strip()
    if publisher and title.endswith(f" - {publisher}"):
        title = title[: -len(publisher) - 3].rstrip()
    return title


def norm_title(title: str) -> str:
    return WS_RE.sub(" ", PUNCT_RE.sub(" ", title.lower())).strip()


def normalize_entry(entry: Entry, *, feed, fetched: datetime, url: str | None = None, summary_chars: int = 500) -> dict:
    link = url or entry.link
    canon = canonical_url(link)
    published = entry.published
    if published is None or published > fetched + FUTURE_TOLERANCE:
        published = fetched
    title = clean_title(entry.title, entry.source_title)
    summary = (entry.summary or "")[:summary_chars]
    if summary.strip() == title.strip():
        summary = ""
    return {
        "id": item_id(canon),
        "source_name": (entry.source_title or feed.name) if "news.google.com" in feed.url else feed.name,
        "source_type": "google_news" if "news.google.com" in feed.url else "publisher",
        "subject_area": feed.subject_area,
        "language": feed.language,
        "title": title,
        "summary": summary,
        "url": link,
        "canonical_url": canon,
        "published_utc": iso(published),
        "fetched_utc": iso(fetched),
        "feed_url": feed.url,
        "raw_categories": entry.categories,
        "feed_id": feed.id,
    }
