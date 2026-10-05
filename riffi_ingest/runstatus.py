"""What `python -m riffi_ingest status` shows (step 7, D-008): is the engine alive, how did its runs go, and
which sources keep failing. Built from the run diary (engine_runs) and the sources table; changes nothing."""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from .db import store
from .db.connection import from_iso
from .fetchers.parse import IST

DAY = timedelta(hours=24)
NORMAL_GAP = timedelta(minutes=45)  # the timer checks every 30 minutes; a longer silence is worth showing
CLOCK_SLACK = timedelta(minutes=5)  # a row dated further ahead than this means the laptop's clock jumped

MODES = {"due": "the sources that were due", "all": "all sources", "source": "chosen sources"}
OUTCOMES = {
    "ok": "ran",
    "offline": "offline (no internet)",
    "failed": "failed",
    "nothing_due": "nothing due",
    "busy": "skipped (a run was still going)",
    "running": "running now",
}


def _n(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _when(at: datetime) -> str:
    return at.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")


def _span(delta: timedelta) -> str:
    minutes = delta.total_seconds() / 60
    if minutes < 1:
        return "under a minute"
    if minutes < 90:
        return f"{minutes:.0f} min"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / 1440:.1f} days"


def report(conn: sqlite3.Connection, now: datetime, run_in_progress: bool = False, timer: bool = True) -> list[str]:
    """`run_in_progress`: whether a fetch holds the run lock right now. Without one, every row still marked
    'running' belongs to a run that was killed or crashed. `timer`: whether Windows' timer is meant to be on
    (config/schedule.yaml); with it off (D-009), gaps between fetches are expected, not a fault."""
    last = store.last_engine_run(conn)
    if last is None:
        if store.first_engine_run_at(conn) is not None:  # only timer checks that found nothing due
            return [
                "No fetch has run yet: every check so far found nothing due. Run one: python -m riffi_ingest fetch --all"
            ]
        return [
            "No runs in the run diary yet (it begins with the first run after the 5 Oct 2026 update)."
            " Run one now with: python -m riffi_ingest fetch --all"
        ]
    current = last["engine_run_id"] if run_in_progress and last["outcome"] == "running" else None
    since = now - DAY
    start = max(since, store.first_engine_run_at(conn))
    return [
        _last_run(last, now, current),
        *_last_day(store.engine_runs_since(conn, since), now, start, current, timer),
        *_failing(store.failing_sources(conn)),
    ]


def _dead(row: sqlite3.Row, current: int | None) -> bool:
    return row["outcome"] == "running" and row["engine_run_id"] != current


def _last_run(row: sqlite3.Row, now: datetime, current: int | None) -> str:
    started = from_iso(row["started_at"])
    head = f"Last run: {_when(started)} ({_span(now - started)} ago), {MODES.get(row['mode'], row['mode'])}"
    if row["outcome"] == "running":
        return head + (": did not finish (stopped or crashed)." if _dead(row, current) else ": running now.")
    took = _span(from_iso(row["finished_at"]) - started)
    if row["outcome"] == "failed":
        return head + f": FAILED after {took} - {row['error']}"
    if row["outcome"] == "offline":
        return head + (
            f": no internet - none of the {row['sources_due']} sources could be reached,"
            " so nothing was counted against them."
        )
    return head + (
        f": done in {took}. {row['sources_due']} sources: {row['sources_ok']} worked, {row['sources_failed']} failed,"
        f" {row['sources_skipped']} skipped; {row['items_new']} new articles."
    )


def _longest_gap(
    rows: list[sqlite3.Row], now: datetime, start: datetime, current: int | None
) -> tuple[timedelta, datetime, datetime]:
    """The longest stretch with no check at all, from the end of one check to the start of the next, so a long
    run is not mistaken for the laptop being off."""
    seen = start
    longest = (timedelta(0), start, start)
    for r in rows:
        began = from_iso(r["started_at"])
        if began - seen > longest[0]:
            longest = (began - seen, seen, began)
        if r["engine_run_id"] == current:
            ended = now
        else:
            ended = from_iso(r["finished_at"]) if r["finished_at"] else began
        seen = max(seen, ended)
    if now - seen > longest[0]:
        longest = (now - seen, seen, now)
    return longest


BY_HAND = "fetching is by hand for now, D-009"


def _last_day(
    rows: list[sqlite3.Row], now: datetime, start: datetime, current: int | None, timer: bool = True
) -> list[str]:
    if not rows:
        if not timer:
            return [f"Last 24 hours: no fetches - {BY_HAND}. Run one: python -m riffi_ingest fetch --all"]
        return ["Last 24 hours: no checks at all - the timer is not running, or the laptop was off or asleep."]
    counts = Counter("did not finish" if _dead(r, current) else OUTCOMES.get(r["outcome"], r["outcome"]) for r in rows)
    lines = [
        f"Last 24 hours: {_n(len(rows), 'check')} - "
        + ", ".join(f"{n} {what}" for what, n in counts.most_common())
        + "."
    ]
    gap, gap_start, gap_end = _longest_gap(rows, now, start, current)
    if gap > NORMAL_GAP and not timer:
        lines.append(f"Longest gap between fetches: {_span(gap)}, {_when(gap_start)} to {_when(gap_end)} ({BY_HAND}).")
    elif gap > NORMAL_GAP:
        lines.append(
            f"Longest gap with no check: {_span(gap)}, {_when(gap_start)} to {_when(gap_end)}"
            " (the laptop was off or asleep, or the timer was not running)."
        )
    else:
        lines.append("No gaps: the engine checked at least every 45 minutes.")
    future = sum(1 for r in rows if from_iso(r["started_at"]) > now + CLOCK_SLACK)
    if future:
        lines.append(
            f"Warning: {future} diary entries are dated in the future - the laptop's clock may have jumped,"
            " so the times above may be wrong."
        )
    refusals = sum(r["google_refusals"] or 0 for r in rows)
    if refusals:
        lines.append(
            f"Google refusals: {refusals} - WARNING: Google is starting to refuse us. Ask Zuko before fetching more."
        )
    else:
        lines.append("Google refusals: 0.")
    return lines


def _failing(rows: list[sqlite3.Row]) -> list[str]:
    if not rows:
        return ["Sources failing 3+ runs in a row: none."]
    lines = [f"Sources failing 3+ runs in a row ({len(rows)}; the brief flags these for replacement):"]
    lines += [
        f"  {r['source_id']} {r['name']} - {r['consecutive_failures']} in a row - {(r['last_status'] or '')[:100]}"
        for r in rows
    ]
    return lines
