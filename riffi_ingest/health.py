"""Health checks on one fetch (BRIEF.md "Health checks", step 9) and a suggested fix for each failure
(BRIEF.md "FIRST RUN", point 2). Used by `test-feeds`; the daily health report reuses its limits.

| Check     | Fails when                                                                 |
|-----------|----------------------------------------------------------------------------|
| Reachable | 403, 404, 5xx, timeout or another network error                            |
| Valid     | not RSS/Atom (feeds) or not HTML (page monitors)                           |
| Alive     | 0 items, or newest item older than 7 days (feeds; page monitors: a page   |
|           | always yields a snapshot, so only reachability and validity apply)        |
| Fields    | entries missing title, link or date (a Telegram post's text counts as its  |
|           | title, as cleaning takes its first sentence: normalise.py)                 |

"Useful" (topic-tagged items in 7 days) needs stored history; it is in the daily health report
(outputs/health_report.py), which also judges page monitors stale only after 30 days.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .fetchers.http import domain_key
from .fetchers.outcome import FetchOutcome

STALE_AFTER = timedelta(days=7)
PAGE_STALE_AFTER = timedelta(days=30)  # BRIEF.md "Alive": a monitored page may rightly not change for weeks
REQUIRED_FIELDS = ("title", "link", "date")
PAGE_MONITOR = "Web page monitor"
TELEGRAM = "RSSHub Telegram"
NEEDS_JS_FIX = "this page needs JavaScript: find a different URL or a JSON endpoint for this source"


@dataclass
class Health:
    passed: bool
    problems: list[str] = field(default_factory=list)
    fix: str = ""
    newest: datetime | None = None
    fields: list[str] = field(default_factory=list)


FIELD_FIXES = {
    "title": "entries have no title, so they cannot be read or matched to topics: check the feed, or use another route",
    "link": "entries have no link, so cleaning drops every one: check the feed, or use another route",
    "date": "entries have no date, so cleaning dates them at fetch time: prefer a feed that dates its items",
}


def _fields(outcome: FetchOutcome) -> list[str]:
    """The fields present on at least one entry, as cleaning will see them (normalise.py): a Telegram post
    has no title of its own, and cleaning takes the first sentence of its text instead."""
    checks = {"title": "title", "link": "link", "date": "published", "summary": "summary", "image": "image_url"}
    present = [name for name, attr in checks.items() if any(getattr(e, attr) for e in outcome.entries)]
    telegram_text = outcome.route_type == TELEGRAM and any((e.summary or "").strip() for e in outcome.entries)
    if telegram_text and "title" not in present:
        present.insert(0, "title")
    return present


def _fix_for_error(outcome: FetchOutcome) -> str:
    reason = (outcome.reason or "").lower()
    status = outcome.http_status
    if outcome.route_type == TELEGRAM and domain_key(outcome.url) == "t.me":
        if status in (403, 429, 503):
            return "t.me is refusing or rate-limiting: fetch Telegram less often, or self-host RSSHub (RSSHUB_BASE_URL)"
        if status in (404, 410) or "no posts" in reason:
            return "Telegram shows no public page for this channel: check the name, and that it is a public channel"
    if outcome.route_type == TELEGRAM:
        if status in (403, 429, 503):
            return "rsshub.app is refusing or rate-limiting: self-host RSSHub and set RSSHUB_BASE_URL"
        if status in (404, 410):
            return "RSSHub cannot find this channel: check the channel name in the URL, and that it is public"
    if status == 404 or status == 410:
        return "wrong URL: the feed has moved; find the current feed link on the site"
    if status in (401, 403):
        return "site blocks automated requests: use the row's Google News backup, or a page monitor"
    if status == 429:
        return "rate-limited: fetch this source less often"
    if status and status >= 500:
        return "the site's server is failing: retry later; if it persists, find another source"
    if outcome.error_kind == "dns":
        return "the domain does not resolve: check the URL for typos or a dead site"
    if outcome.error_kind == "tls":
        return "the site's security certificate is broken: report it; never switch checking off"
    if outcome.error_kind in ("timeout", "domain_down", "connect"):
        return "the site did not answer in time: retry later; if it persists, the site may block bots"
    if outcome.error_kind == "blocked":
        return "the URL points at X, Instagram or WhatsApp, which are never fetched: use the Google News backup"
    if "html page" in reason:
        return "the URL is a web page, not a feed: find the site's RSS link, or switch the row to a page monitor"
    if "not a web page" in reason:
        return "the page is a PDF or file: monitor the page that links to it instead"
    if "needs javascript" in reason:
        return NEEDS_JS_FIX
    if "bot check" in reason:
        return "the site shows a bot check: try a different page or route for this source"
    return "check the URL by hand"


def _fix_for_skip(outcome: FetchOutcome) -> str:
    reason = outcome.reason or ""
    if "YouTube channel ID" in reason:
        return "add the channel's ID: https://www.youtube.com/feeds/videos.xml?channel_id=UC..."
    if "no backup Google News URL" in reason:
        return "add a backup_google_news_url (a Google News search for the handle's name)"
    if "not a Google News feed" in reason:
        return "set backup_google_news_url to a news.google.com/rss/search?q=... link (X/Instagram are never fetched)"
    if "not a URL" in reason:
        return "replace the instruction in fetch_url with the actual URL"
    if "manual" in reason:
        return "manual source: nothing to fetch"
    return reason


def check(outcome: FetchOutcome, now: datetime) -> Health:
    """Pass/fail for one fetch, the problems found, and a suggested fix."""
    if outcome.status == "skipped":
        manual = "manual" in (outcome.reason or "")
        return Health(
            passed=manual, problems=[] if manual else [f"skipped: {outcome.reason}"], fix=_fix_for_skip(outcome)
        )
    if outcome.status == "not_modified":
        return Health(passed=True)
    if outcome.status == "error":
        return Health(passed=False, problems=[outcome.reason or "error"], fix=_fix_for_error(outcome))
    if outcome.route_type == PAGE_MONITOR:
        if outcome.snapshot is not None and not outcome.snapshot.text.strip():
            return Health(
                passed=False,
                problems=["no readable text on the page"],
                fix=NEEDS_JS_FIX,
            )
        return Health(passed=True)
    fields = _fields(outcome)
    dated = [e.published for e in outcome.entries if e.published]
    newest = max(dated) if dated else None
    health = Health(passed=True, newest=newest, fields=fields)
    if not outcome.entries:
        health.problems.append("0 items")
        if "news.google.com" in (outcome.url or ""):
            health.fix = "the Google News query finds nothing: broaden it, or use the publisher's own feed"
        else:
            health.fix = "the feed is empty: check it still publishes, or find a fresher feed"
    elif newest is not None and now - newest > STALE_AFTER:
        health.problems.append(f"newest item is {(now - newest).days} days old")
        health.fix = "the feed has gone quiet: check for a newer feed URL"
    missing = [f for f in REQUIRED_FIELDS if f not in fields]
    if outcome.entries and missing:
        health.problems.append("missing " + ", ".join(missing))
        health.fix = health.fix or "; ".join(FIELD_FIXES[f] for f in missing)
    health.passed = not health.problems
    return health
