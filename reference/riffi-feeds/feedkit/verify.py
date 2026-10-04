"""Phase 1 rules: fetch one feed, parse it, and classify it.

Statuses
  WORKING           parses, has entries, newest item < 3 days old (< 14 for low-frequency sources)
  WORKING_NEEDS_UA  (historic) worked only with a desktop browser user agent; since 29 Sep 2026
                    the fetcher never imitates a browser, so this status is no longer produced
  LAGGING           parses and is dated, but the newest item sits between the freshness
                    limit and 30 days (not in the original brief; not counted as working)
  STALE             parses, but the newest item is more than 30 days old
  UNDATED           parses and has entries, but no entry carries a date, so freshness
                    cannot be proven (not counted as working)
  EMPTY             parses but has 0 entries
  NOT_A_FEED        answers with HTML or anything that is not a feed
  BROKEN            4xx/5xx, timeout, DNS or TLS failure
  BLOCKED_BY_ROBOTS the site's robots.txt disallows the URL: it is not fetched at all
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from . import gnews
from .net import PoliteClient, RobotsCache
from .parse import FeedStats, _parse_date, iso, parse_feed, parse_loose_date, utcnow

FRESH_DAYS = 3
LOW_FREQ_DAYS = 14
STALE_DAYS = 30
GN_MIN_SHARE_24H = 0.30
WORKING = {"WORKING", "WORKING_NEEDS_UA"}

LOW_FREQ_DOMAINS = (
    "rbi.org.in",
    "sebi.gov.in",
    "mongabay.com",
    "nseindia.com",
    "bseindia.com",
    "gstcouncil.gov.in",
    "incometaxindia.gov.in",
    "egazette.gov.in",
    "eci.gov.in",
    "karnataka.gov.in",
    "bmrc.co.in",
    "caravanmagazine.in",
    "frontline.thehindu.com",
    "article-14.com",
    "restofworld.org",
    "the-ken.com",
    "themorningcontext.com",
    "thearcweb.com",
    "capitalmind.in",
    # alert feeds are event-driven: quiet days are healthy
    "sachet.ndma.gov.in",
    "cap-sources.s3.amazonaws.com",
)
LOW_FREQ_TYPES = ("youtube", "podcast", "substack", "newsletter")


def is_low_frequency(url: str, feed_type: str = "", flag: str | bool = "") -> bool:
    if str(flag).lower() in ("1", "true", "yes"):
        return True
    host = (urlsplit(url).hostname or "").lower()
    if any(host == d or host.endswith("." + d) for d in LOW_FREQ_DOMAINS):
        return True
    ft = (feed_type or "").lower()
    return any(t in ft for t in LOW_FREQ_TYPES) or "youtube.com/feeds" in url


def classify(stats: FeedStats, now: datetime, low_freq: bool) -> str:
    if not stats.is_feed:
        return "NOT_A_FEED"
    if stats.entries_count == 0:
        return "EMPTY"
    if stats.newest is None:
        return "UNDATED"
    age = now - stats.newest
    if age > timedelta(days=STALE_DAYS):
        return "STALE"
    if age <= timedelta(days=LOW_FREQ_DAYS if low_freq else FRESH_DAYS):
        return "WORKING"
    return "LAGGING"


PAGE_DATE_PATTERNS = (
    re.compile(
        r'(?:article:published_time|datePublished|pubdate|publish-date|dc\.date)["\']?\s*(?:content|:)\s*=?\s*["\']([^"\']{8,40})["\']',
        re.I,
    ),
    re.compile(r"Posted On:\s*([0-9]{1,2}\s+[A-Za-z]{3,9}\s+[0-9]{4})", re.I),  # PIB
    re.compile(r"Date\s*:\s*([A-Za-z]{3,9}\s+[0-9]{1,2},\s*[0-9]{4})", re.I),  # RBI
    # PIB prints "26 SEP 2026 8:11PM by PIB Delhi", often split from its label by tags
    re.compile(
        r"\b([0-9]{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*,?\s+20[0-9]{2}(?:\s+[0-9]{1,2}:[0-9]{2}\s*[AP]M)?)",
        re.I,
    ),
)


async def infer_date_from_item_page(client: PoliteClient, robots: RobotsCache, link: str) -> datetime | None:
    """For feeds whose items carry no dates: read the date off the newest item's page, as the
    bot and only where the site's robots.txt allows it."""
    try:
        allowed, _note = await robots.allowed(link)
    except Exception:  # an unreadable robots.txt is not a yes
        return None
    if allowed != "yes":
        return None
    res = await client.fetch(link, ua="bot", retries=1)
    if not res.ok:
        return None
    text = res.content[:400_000].decode("utf-8", errors="ignore")
    for pattern in PAGE_DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = m.group(1).strip()
            dt = (
                _parse_date(raw)
                or parse_loose_date(raw.title())
                or parse_loose_date(raw.title().replace("Am", "AM").replace("Pm", "PM"))
            )
            if dt:
                return dt
    return None


async def verify_url(
    client: PoliteClient,
    robots: RobotsCache,
    url: str,
    *,
    low_freq: bool = False,
    gn_samples: int = 3,
    reddit_cache: dict | None = None,
    now: datetime | None = None,
) -> dict:
    out: dict = {"url": url, "flags": []}
    try:
        out["robots_allowed"], out["robots_note"] = await robots.allowed(url)
    except Exception as exc:  # robots problems must never sink a feed check
        out["robots_allowed"], out["robots_note"] = "unknown", f"robots error: {exc}"[:200]

    now = now or utcnow()
    # Reddit is never asked from here (reddit.com's robots answer is always "no" in RobotsCache): it
    # is read only through fetcher.reddit_signal.guarded_fetch, with the shared turn and pause
    if out["robots_allowed"] != "yes":  # no, or robots.txt unreachable (RFC 9309: assume no): do not fetch
        unreachable = out["robots_allowed"] != "no"
        out.update(
            status="ROBOTS_UNREACHABLE" if unreachable else "BLOCKED_BY_ROBOTS",
            checked_utc=iso(now),
            http_status=None,
            needs_browser_ua=False,
            low_frequency=low_freq,
            error="robots.txt could not be read (assume no)" if unreachable else "robots.txt disallows this URL",
            error_kind="robots",
        )
        return out

    # one request, as the bot: a site that refuses the bot is not asked again in disguise
    chosen = await client.fetch(url, ua="bot")
    needs_ua = False
    out.update(
        final_url=chosen.final_url or url,
        http_status=chosen.status,
        bot_status=chosen.status if chosen.error is None else chosen.error_kind,
        content_type=chosen.content_type.split(";")[0].strip(),
        ua_used=chosen.ua,
        needs_browser_ua=needs_ua,
        error=chosen.error,
        error_kind=chosen.error_kind,
        redirects=chosen.redirects,
        supports_etag=bool(chosen.headers.get("etag")),
        supports_last_modified=bool(chosen.headers.get("last-modified")),
        checked_utc=iso(now),
        low_frequency=low_freq,
    )

    stats = FeedStats()
    if chosen.ok:
        stats = parse_feed(chosen.content)
        stats.compute(now)
    if not chosen.ok:
        status = "BROKEN"
    elif stats.kind == "sitemap" and stats.child_sitemaps and not stats.is_feed:
        status = "NOT_A_FEED"
        out["flags"].append("SITEMAP_INDEX")
    elif not stats.is_feed:
        status = "NOT_A_FEED"
    else:
        status = classify(stats, now, low_freq)
    if status == "UNDATED":
        first_link = next((e.link for e in stats.entries if e.link.startswith("http")), "")
        page_date = await infer_date_from_item_page(client, robots, first_link) if first_link else None
        if page_date:
            stats.newest = page_date
            out["flags"].append("DATE_FROM_ITEM_PAGE")
            status = classify(stats, now, low_freq)
    out["status"] = status
    out.update(
        is_feed=stats.is_feed,
        feed_kind=stats.kind,
        feed_version=stats.version,
        feed_title=stats.feed_title,
        bozo=stats.bozo,
        bozo_reason=stats.bozo_reason,
        entries_count=stats.entries_count,
        dated_count=stats.dated_count,
        future_dated=stats.future_dated,
        newest_utc=iso(stats.newest),
        oldest_utc=iso(stats.oldest),
        items_per_day=stats.items_per_day,
        share_24h=stats.share_24h,
        pct_title=stats.pct_title,
        pct_link=stats.pct_link,
        pct_date=stats.pct_date,
        pct_summary=stats.pct_summary,
        pct_content=stats.pct_content,
        has_full_text=stats.has_full_text,
        child_sitemaps=stats.child_sitemaps[:50],
        samples=[
            {
                "title": e.title,
                "summary": e.summary[:240],
                "link": e.link,
                "published": iso(e.published),
                "source": e.source_title,
            }
            for e in stats.entries[:100]
        ],
    )
    if stats.future_dated:
        out["flags"].append(f"FUTURE_DATED_ITEMS:{stats.future_dated}")

    if gnews.is_gnews(url) and stats.is_feed:
        if stats.share_24h is not None and stats.share_24h < GN_MIN_SHARE_24H:
            out["flags"].append("GN_LOW_24H_SHARE")
        resolved = []
        for e in stats.entries[:gn_samples]:
            real, how = await gnews.resolve(client, e.link)
            resolved.append({"gn_url": e.link, "resolved": real, "method": how, "title": e.title})
        out["gn_samples"] = resolved
        if resolved:
            out["gn_resolve_rate"] = round(sum(1 for r in resolved if r["resolved"]) / len(resolved), 2)

    return out
