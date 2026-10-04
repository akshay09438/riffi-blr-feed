"""Health check (every 6h by default): re-verify every feed with the Phase 1 rules, record
status, last_checked_utc and the live robots.txt answer, and alert when a P0 feed goes STALE or
BROKEN. It never switches a feed to another address by itself (29 Sep 2026: the old automatic
repair could land on Google News or a page the site forbids); a person decides."""

from __future__ import annotations

import asyncio
import csv
import os
from datetime import datetime, timedelta
from pathlib import Path

from feedkit.net import PoliteClient, RobotsCache
from feedkit.parse import iso, utcnow
from feedkit.verify import WORKING, verify_url

from .config import HEALTH_LOG, ROOT, Settings
from .db import DB

ALERT_STATUSES = {"STALE", "BROKEN"}
REALERT_AFTER = timedelta(hours=24)
VERIFIED_CSV = ROOT / "output" / "feeds_verified.csv"


def _line(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=utcnow().tzinfo)


async def run_health(
    settings: Settings,
    db: DB,
    client: PoliteClient,
    *,
    log_path: Path = HEALTH_LOG,
    verified_csv: Path = VERIFIED_CSV,
    concurrency: int = 8,
) -> list[dict]:
    db.sync_feeds(settings.feeds)
    robots = RobotsCache(client)
    now = utcnow()
    sem = asyncio.Semaphore(concurrency)
    results: list[dict] = []

    async def one(feed):
        state = db.feed_state(feed.id)
        url = feed.url  # always the approved address (verify_url checks robots.txt before fetching)
        async with sem:
            try:
                res = await verify_url(client, robots, url, low_freq=feed.low_frequency, gn_samples=0)
            except Exception as exc:  # one bad feed must not stop the pass
                _line(log_path, f"{iso(utcnow())} ERROR checking {feed.id} {url}: {type(exc).__name__}: {exc}"[:300])
                res = {"status": "BROKEN", "checked_utc": iso(utcnow()), "error": f"{type(exc).__name__}"}
        prev = state["status"] if state else None
        live = {"no": "no", "yes": "yes"}.get(res.get("robots_allowed"))
        fields = {"status": res["status"], "last_checked_utc": res["checked_utc"]}
        if live:
            fields["robots_live"] = live  # the poller stops (or resumes) on this answer
        db.update_feed(feed.id, **fields)
        record = {"id": feed.id, "status": res["status"], "last_checked_utc": res["checked_utc"], "url": url}
        results.append(record)
        if feed.priority != "P0" or res["status"] not in ALERT_STATUSES:
            return
        last_alert = _parse_iso(state["last_alert_utc"]) if state else None
        if prev == res["status"] and last_alert and now - last_alert < REALERT_AFTER:
            return
        detail = res.get("error") or f"http {res.get('http_status')}, newest item {res.get('newest_utc') or 'none'}"
        _line(
            log_path, f"{iso(now)} ALERT P0 {feed.id} '{feed.name}' {prev or 'new'} -> {res['status']} ({detail}) {url}"
        )
        db.update_feed(feed.id, last_alert_utc=iso(now))
        # no automatic switch to another address: a replacement is chosen by a person

    from .poller import eligible  # the same rule as polling: never contact a feed we may not poll

    feeds = [
        f
        for f in settings.feeds
        if eligible(settings, f)[0] and "youtube.com/feeds" not in f.url and not f.topic_filter
    ]
    await asyncio.gather(*(one(f) for f in feeds))
    _update_verified_csv(verified_csv, results)
    working = sum(1 for r in results if r["status"] in WORKING)
    _line(log_path, f"{iso(utcnow())} HEALTH checked {len(results)} feeds, {working} working")
    return results


def _update_verified_csv(path: Path, results: list[dict]) -> None:
    """Keep output/feeds_verified.csv's status and last_checked_utc current."""
    if not path.exists():
        return
    by_id = {r["id"]: r for r in results}
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields, rows = reader.fieldnames or [], list(reader)
    for row in rows:
        r = by_id.get(row.get("id"))
        if r:
            row["status"], row["last_checked_utc"] = r["status"], r["last_checked_utc"]
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)
