"""Fetch every source, one route type at a time (BRIEF.md step 1).

| route_type                 | what is fetched                                                        |
|----------------------------|------------------------------------------------------------------------|
| Native publisher RSS/Atom  | fetch_url                                                              |
| Google News RSS            | fetch_url                                                              |
| X/Instagram via RSS.app    | never X or Instagram itself: backup_google_news_url, marked on_backup |
| RSSHub Telegram            | the channel's public page t.me/s/<name> (telegram.py); or, when        |
|                            | RSSHUB_BASE_URL is set, fetch_url re-pointed at that RSSHub             |
| YouTube Atom               | fetch_url, skipped when it is not a real URL (e.g. "Needs channel ID") |
| Web page monitor           | fetch_url, compared with its last snapshot (pagemonitor.py)            |
| Manual                     | skipped                                                                |

One bad source never stops the run: every problem becomes an outcome with an error, never an exception.
"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit, urlunsplit

from ..config import DEFAULT_RSSHUB_BASE
from ..sources import Source
from .gnews import is_gnews_feed
from .http import PAGE_ACCEPT, PoliteClient, domain_key
from .outcome import FetchOutcome, PageSnapshot, Validators
from .pagemonitor import monitor_page
from .parse import looks_like_html, parse_feed
from .telegram import PAGE_BASE, page_url, parse_page

NATIVE = "Native publisher RSS/Atom"
GOOGLE_NEWS = "Google News RSS"
X_INSTAGRAM = "X/Instagram via RSS.app"
TELEGRAM = "RSSHub Telegram"
YOUTUBE = "YouTube Atom"
PAGE_MONITOR = "Web page monitor"
MANUAL = "Manual"
FETCHED_ROUTES = {NATIVE, GOOGLE_NEWS, X_INSTAGRAM, TELEGRAM, YOUTUBE, PAGE_MONITOR}


def _is_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in ("http", "https") and bool(parts.netloc) and " " not in value


def rss_app_url(source: Source) -> str:
    """Stub for later: RSS.app feeds will be added only for rows where priority_x_feed = yes (5 rows).
    Until then every X/Instagram row runs on its backup Google News query."""
    return ""


def _with_base(url: str, base: str) -> str:
    b = urlsplit(base)
    u = urlsplit(url)
    return urlunsplit((b.scheme, b.netloc, b.path.rstrip("/") + u.path, u.query, ""))


def plan(source: Source, rsshub_base: str = DEFAULT_RSSHUB_BASE) -> tuple[str, bool, str]:
    """Decide what to fetch for a source: (url, on_backup, skip_reason). An empty url means skip."""
    route = source.route_type
    if route == MANUAL:
        return "", False, "manual source"
    if route not in FETCHED_ROUTES:
        return "", False, f"unknown route type {route!r}"
    if route == X_INSTAGRAM:
        if direct := rss_app_url(source):
            return direct, False, ""
        if not _is_url(source.backup_google_news_url):
            return "", True, "no backup Google News URL (X and Instagram are never scraped)"
        if not is_gnews_feed(source.backup_google_news_url):
            return "", True, f"backup is not a Google News feed: {source.backup_google_news_url!r}"
        return source.backup_google_news_url, True, ""
    if not _is_url(source.fetch_url):
        if route == YOUTUBE:
            return "", False, f"needs a YouTube channel ID (fetch_url is {source.fetch_url!r})"
        return "", False, f"fetch_url is not a URL: {source.fetch_url!r}"
    if route == TELEGRAM:
        if rsshub_base:
            return _with_base(source.fetch_url, rsshub_base), False, ""
        if not (url := page_url(source.fetch_url)):
            return "", False, f"no Telegram channel name in {source.fetch_url!r}"
        return url, False, ""
    return source.fetch_url, False, ""


async def _fetch_feed(client: PoliteClient, out: FetchOutcome, v: Validators) -> None:
    res = await client.fetch(out.url, etag=v.etag, last_modified=v.last_modified)
    out.http_status, out.error_kind = res.status, res.error_kind
    if res.not_modified:
        out.status, out.validators = "not_modified", v
    elif not res.ok:
        out.status, out.reason = "error", res.error or f"HTTP {res.status}"
    elif looks_like_html(res.content, res.content_type):
        out.status, out.reason = "error", "answered with an HTML page, not a feed"
    else:
        feed = parse_feed(res.content)
        if not feed.is_feed:
            out.status, out.reason = "error", feed.bozo_reason or "not a feed"
        else:
            out.status, out.feed_title, out.entries = "ok", feed.title, feed.entries
            out.validators = Validators(res.etag, res.last_modified)


async def _fetch_telegram_page(client: PoliteClient, out: FetchOutcome) -> None:
    res = await client.fetch(out.url, accept=PAGE_ACCEPT)
    out.http_status, out.error_kind = res.status, res.error_kind
    if not res.ok:
        out.status, out.reason = "error", res.error or f"HTTP {res.status}"
        return
    title, entries, posts = parse_page(res.content)
    if not posts:
        out.status, out.reason = "error", "the channel page shows no posts (not public, or Telegram changed the page)"
    else:
        out.status, out.feed_title, out.entries = "ok", title, entries


async def fetch_source(
    client: PoliteClient,
    source: Source,
    *,
    rsshub_base: str = DEFAULT_RSSHUB_BASE,
    validators: Validators | None = None,
    snapshot: PageSnapshot | None = None,
) -> FetchOutcome:
    """Fetch one source. `snapshot` is the page as last seen (page monitors only). Never raises."""
    url, on_backup, skip = plan(source, rsshub_base)
    out = FetchOutcome(source.source_id, source.route_type, "skipped", url=url, on_backup=on_backup, reason=skip)
    if not url:
        return out
    started = time.monotonic()
    v = validators or Validators()
    try:
        if source.route_type == PAGE_MONITOR:
            out = await monitor_page(client, source, url, previous=snapshot, validators=v)
        elif url.startswith(PAGE_BASE):
            await _fetch_telegram_page(client, out)
        else:
            await _fetch_feed(client, out, v)
    except Exception as exc:  # a parser bug on one odd source must not stop the other 130
        out.status, out.reason = "error", f"{type(exc).__name__}: {exc}"[:300]
        out.snapshot, out.entries = snapshot, []  # a page keeps its last snapshot
    out.elapsed = round(time.monotonic() - started, 2)
    return out


async def fetch_sources(
    client: PoliteClient,
    sources: list[Source],
    *,
    rsshub_base: str = DEFAULT_RSSHUB_BASE,
    validators: dict[str, Validators] | None = None,
    snapshots: dict[str, PageSnapshot] | None = None,
    on_done=None,
) -> list[FetchOutcome]:
    """Fetch many sources: different sites in parallel, each site's sources one after another.

    One at a time per site matters: the client allows one request per site every 2 s and counts a
    request's 60 s limit from when it starts waiting for its turn, so queueing all 84 Google News feeds at
    once would time most of them out before they were ever sent. Outcomes come back in input order.
    `validators` and `snapshots` are keyed by source_id; `on_done(outcome)` is called as each finishes."""
    validators, snapshots = validators or {}, snapshots or {}
    by_site: dict[str, list[int]] = {}
    for n, s in enumerate(sources):
        url = plan(s, rsshub_base)[0]
        by_site.setdefault(domain_key(url) if url else f"skip:{n}", []).append(n)
    results: list[FetchOutcome | None] = [None] * len(sources)

    async def one_site(indexes: list[int]) -> None:
        for n in indexes:
            s = sources[n]
            results[n] = await fetch_source(
                client,
                s,
                rsshub_base=rsshub_base,
                validators=validators.get(s.source_id),
                snapshot=snapshots.get(s.source_id),
            )
            if on_done is not None:
                on_done(results[n])

    await asyncio.gather(*(one_site(ix) for ix in by_site.values()))
    return [r for r in results if r is not None]
