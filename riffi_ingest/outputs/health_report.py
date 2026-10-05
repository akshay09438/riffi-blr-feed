"""The feed health report (BRIEF.md "Feed health checks and the daily report", steps 8 and 9):
reports/YYYY-MM-DD/health.md and sources_health.csv.

Who it is for: the founder and the content team. health.md answers "is the engine alive, and which sources are
broken, quiet or useless"; sources_health.csv holds the four health columns to paste into the source sheet
(last_status, last_ok_at, newest_item_at, fields_present), one row per active source in source_id order.

It reads the health columns every fetch keeps on the sources table, so it is always "as of now": a past day's
source health cannot be rebuilt, and the report says so when asked for one. Nothing is ever switched or
deleted here: flagged sources are for a person to fix or replace (BRIEF.md; the day-14 decisions are
recommendations).
"""

from __future__ import annotations

import csv
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..db.connection import from_iso
from ..fetchers.parse import IST
from ..health import PAGE_MONITOR, PAGE_STALE_AFTER, STALE_AFTER
from . import queries
from .digest import at_text, cell, day_text

FAILING_IN_A_ROW = 3  # BRIEF.md: flagged for replacement after 3 consecutive failures
USEFUL_WINDOW = queries.QUIET_AFTER  # BRIEF.md "Useful": 0 topic-tagged items in 7 days fails
X_ROUTE = "X/Instagram via RSS.app"
MANUAL_ROUTE = "Manual"
HEALTH_FIELDS = ("source_id", "name", "last_status", "last_ok_at", "newest_item_at", "fields_present")

# Each active source sits in exactly one of these, in this order of precedence.
FAILING, FAILED, STALE, SKIPPED, NEVER, MANUAL, WORKING = (
    "failing", "failed", "stale", "skipped", "never", "manual", "working",
)  # fmt: skip
SECTIONS = {
    FAILING: "Failing 3+ runs in a row (flagged for replacement)",
    FAILED: "Failed the last run (not yet 3 in a row)",
    STALE: "Stale: newest item older than 7 days (page monitors: 30 days)",
    SKIPPED: "Skipped: input gaps a person must fix in feeds.csv",
    NEVER: "Never fetched yet",
}
SUMMARY_WORDS = {
    WORKING: "working",
    FAILING: "failing 3+ in a row",
    FAILED: "failed the last run",
    STALE: "stale",
    SKIPPED: "skipped (input gaps)",
    NEVER: "never fetched",
    MANUAL: "manual (nothing to fetch)",
}


@dataclass
class SourceState:
    row: sqlite3.Row
    category: str
    detail: str = ""
    useful: bool = True  # had at least one topic-tagged story in the last 7 days


@dataclass
class HealthResult:
    md_path: Path
    csv_path: Path
    categories: Counter = field(default_factory=Counter)
    not_useful: int = 0


def ist_text(stored: str | None) -> str:
    """A stored UTC time as 'YYYY-MM-DD HH:MM' IST, the way the source sheet shows times; '' when empty."""
    when = from_iso(stored)
    return when.astimezone(IST).strftime("%Y-%m-%d %H:%M") if when else ""


def _age(stored: str, now: datetime) -> str:
    days = (now - from_iso(stored)).days
    return f"{days} day{'s' if days != 1 else ''} ago"


def classify(row: sqlite3.Row, now: datetime) -> SourceState:
    """Which health group a source is in, from the columns every fetch keeps up to date."""
    status = row["last_status"] or ""
    if row["route_type"] == MANUAL_ROUTE or (status.startswith("skipped") and "manual" in status):
        return SourceState(row, MANUAL)
    if not status:
        return SourceState(row, NEVER)
    if status.startswith("skipped"):
        return SourceState(row, SKIPPED, status)
    if row["consecutive_failures"] >= FAILING_IN_A_ROW:
        return SourceState(row, FAILING, f"{row['consecutive_failures']} in a row - {status[:120]}")
    if status.startswith("error"):
        return SourceState(row, FAILED, status[:120])
    page = row["route_type"] == PAGE_MONITOR
    newest = row["newest_item_at"]
    if newest and now - from_iso(newest) > (PAGE_STALE_AFTER if page else STALE_AFTER):
        return SourceState(row, STALE, f"newest item {ist_text(newest)[:10]} ({_age(newest, now)})")
    if not newest and not page:  # a monitored page that has not changed yet is not stale
        return SourceState(row, STALE, "no dated item yet")
    return SourceState(row, WORKING)


def assess(conn: sqlite3.Connection, now: datetime) -> list[SourceState]:
    tagged = queries.tagged_stories_by_source(conn, now - USEFUL_WINDOW, now)
    states = [classify(r, now) for r in queries.source_health(conn)]
    for s in states:
        if s.category in (WORKING, STALE):
            s.useful = tagged.get(s.row["source_id"], 0) > 0
    return states


def _bullet(s: SourceState) -> str:
    detail = f" - {cell(s.detail)}" if s.detail else ""
    return f"- {s.row['source_id']} {cell(s.row['name'])}{detail}"


def _engine_section(engine_lines: list[str]) -> list[str]:
    lines = ["## Engine", ""]
    in_failing = False
    for line in engine_lines:
        # the failing sources are listed under Sources below; status's own list would only repeat them
        if line.startswith("Sources failing 3+ runs in a row"):
            in_failing = True
            continue
        if in_failing and line.startswith(" "):
            continue
        in_failing = False
        lines.append(("  - " if line.startswith(" ") else "- ") + cell(line.strip()))
    return lines + [""]


def _sources_section(states: list[SourceState], now: datetime, history: datetime | None) -> list[str]:
    counts = Counter(s.category for s in states)
    summary = ", ".join(f"{counts[k]} {w}" for k, w in SUMMARY_WORDS.items() if counts[k])
    lines = ["## Sources", "", f"Of {len(states)} active sources: {summary or 'none'}."]
    non_x = [s for s in states if s.row["route_type"] not in (X_ROUTE, MANUAL_ROUTE)]
    if non_x:
        working = sum(s.category == WORKING for s in non_x)
        lines.append(
            f"Passing health checks: {working} of {len(non_x)} non-X sources ({100 * working / len(non_x):.0f}%;"
            " the target is 90%)."
        )
    lines.append("")
    for key, title in SECTIONS.items():
        group = [s for s in states if s.category == key]
        lines += [f"### {title}", ""] + ([_bullet(s) for s in group] or ["None."]) + [""]
    useless = [s for s in states if not s.useful]
    lines += ["### Not useful: 0 topic-tagged stories in 7 days (flagged for replacement)", ""]
    if history is None or now - history < USEFUL_WINDOW:
        since = day_text(history.astimezone(IST).date()) if history else "today"
        lines += [f"History starts {since}, less than 7 days ago: this counts only what was fetched since then.", ""]
    lines += ([_bullet(s) for s in useless] or ["None."]) + [""]
    return lines


def _blocklist_section(problems: list[str]) -> list[str]:
    lines = ["## Blocklist", ""]
    if not problems:
        return lines + ["Every row of blocklist.csv names a domain, so every row is applied.", ""]
    lines.append(
        "These rows cannot be applied until a person adds the site's domain (protected file: ask the founder):"
    )
    return lines + [f"- {cell(p)}" for p in problems] + [""]


def markdown(
    states: list[SourceState],
    *,
    now: datetime,
    day,
    engine_lines: list[str],
    blocklist_problems: list[str],
    history: datetime | None,
) -> str:
    lines = [f"# Riffi feed health, {day_text(day)} (as of {at_text(now)} IST)", ""]
    if day != now.astimezone(IST).date():
        lines += [
            f"Source health and the engine's status are as of when this report was made, not as of {day_text(day)}:"
            " they cannot be rebuilt for a past day.",
            "",
        ]
    lines += _engine_section(engine_lines) + _sources_section(states, now, history)
    lines += _blocklist_section(blocklist_problems)
    return "\n".join(lines).rstrip() + "\n"


def csv_row(row: sqlite3.Row) -> dict:
    return {
        "source_id": row["source_id"],
        "name": row["name"],
        "last_status": row["last_status"] or "",
        "last_ok_at": ist_text(row["last_ok_at"]),
        "newest_item_at": ist_text(row["newest_item_at"]),
        "fields_present": row["fields_present"] or "",
    }


def write_health(
    conn: sqlite3.Connection,
    folder: Path,
    *,
    now: datetime,
    day,
    engine_lines: list[str],
    blocklist_problems: list[str],
) -> HealthResult:
    """Write health.md and sources_health.csv into `folder`, replacing any earlier copy."""
    states = assess(conn, now)
    text = markdown(
        states,
        now=now,
        day=day,
        engine_lines=engine_lines,
        blocklist_problems=blocklist_problems,
        history=queries.history_start(conn),
    )
    folder.mkdir(parents=True, exist_ok=True)
    md_path, csv_path = folder / "health.md", folder / "sources_health.csv"
    md_path.write_text(text, encoding="utf-8")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:  # the BOM lets Excel show Kannada names
        writer = csv.DictWriter(f, fieldnames=HEALTH_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(s.row) for s in states)
    return HealthResult(md_path, csv_path, Counter(s.category for s in states), sum(1 for s in states if not s.useful))
