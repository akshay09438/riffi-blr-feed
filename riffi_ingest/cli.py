"""Command line (BRIEF.md step 7): python -m riffi_ingest <command>.

    import-sources    seed / refresh sources from feeds.csv (idempotent)
    import-topics     seed / refresh topics from topics.csv (idempotent)
    test-feeds        fetch every source once and report pass / fail with a suggested fix; stores nothing
    fetch             one full cycle (fetch, clean, group, tag, store): --all, or --source S004 --source S011

Coming with later steps: digest --date, report, and the scheduler.
"""

from __future__ import annotations

import asyncio
import csv
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import typer

from . import health
from .config import rsshub_base
from .db import connect, store
from .db.connection import PROJECT_ROOT, default_db_path
from .db.importers import import_sources, import_topics
from .fetchers.feeds import fetch_sources, plan
from .fetchers.gnews import ONLINE_CAP_PER_RUN
from .fetchers.http import DOMAIN_INTERVAL, PoliteClient, domain_key
from .fetchers.parse import IST
from .pipeline import Paths, run_fetch
from .runlock import AlreadyRunning, run_lock
from .sources import Source, load_sources

app = typer.Typer(help="Riffi news ingestion engine.", no_args_is_help=True, add_completion=False)

# Defaults are anchored on the project folder, so the commands work from any current folder
# (Windows Task Scheduler starts in System32).
DbOption = typer.Option(
    None, "--db", help="The SQLite database file [default: <project>/data/engine.db or RIFFI_DB_PATH]."
)
FeedsOption = typer.Option(PROJECT_ROOT / "feeds.csv", "--feeds", help="The source list.")
TopicsOption = typer.Option(PROJECT_ROOT / "topics.csv", "--topics", help="The topic list.")
SourceOption = typer.Option(None, "--source", help="Only these source ids (repeatable).")
ReportsOption = typer.Option(PROJECT_ROOT / "reports" / "test-feeds", "--out", help="Folder for the reports.")
AllOption = typer.Option(False, "--all", help="Fetch every active source.")


@app.command("import-sources")
def import_sources_cmd(feeds: Path = FeedsOption, db: Path = DbOption) -> None:
    """Seed or refresh the sources table from feeds.csv. Safe to run again: history is kept."""
    conn = connect(db)
    r = import_sources(conn, feeds)
    typer.echo(f"Sources: {r.added} added, {r.updated} updated, {r.deactivated} marked inactive (left {feeds}).")


@app.command("import-topics")
def import_topics_cmd(topics: Path = TopicsOption, db: Path = DbOption) -> None:
    """Seed or refresh the topics table from topics.csv. Safe to run again."""
    conn = connect(db)
    r = import_topics(conn, topics)
    typer.echo(f"Topics: {r.added} added, {r.updated} updated.")


def _fail(message: str) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=1)


def _read_feeds(feeds: Path) -> list[Source]:
    if not feeds.exists():
        _fail(f"cannot find {feeds}. Run this inside the project folder, or pass --feeds <path to feeds.csv>.")
    return load_sources(feeds)


def _pick(sources: list[Source], wanted: list[str] | None) -> list[Source]:
    if not wanted:
        return sources
    ids = [w.strip().upper() for w in wanted]
    known = {s.source_id.upper() for s in sources}
    unknown = [w for w in ids if w not in known]
    if unknown:
        _fail(f"no such source id: {', '.join(unknown)} (see source_id in feeds.csv)")
    return [s for s in sources if s.source_id.upper() in set(ids)]


def _estimate_minutes(sources: list[Source], with_lookups: bool) -> int:
    """The busiest site sets the pace: one request per site every ~2.25 s (Google News carries 84 sources)."""
    per_site = Counter(domain_key(url) for url, _, _ in (plan(s, rsshub_base()) for s in sources) if url)
    seconds = max(per_site.values(), default=0) * sum(DOMAIN_INTERVAL) / 2
    if with_lookups:
        seconds += ONLINE_CAP_PER_RUN * 2 * sum(DOMAIN_INTERVAL) / 2  # Google News link look-ups, 2 requests each
    return max(1, round(seconds / 60))


def _progress(total: int):
    done = 0

    def report(outcome) -> None:
        nonlocal done
        done += 1
        detail = f"{len(outcome.entries)} items" if outcome.status == "ok" else (outcome.reason or outcome.status)
        typer.echo(f"  [{done:>3}/{total}] {outcome.source_id:<5} {outcome.status:<12} {detail[:70]}")

    return report


def _date(when: datetime | None) -> str:
    return when.astimezone(IST).strftime("%Y-%m-%d %H:%M") if when else ""


@app.command("test-feeds")
def test_feeds(
    feeds: Path = FeedsOption,
    source: list[str] = SourceOption,
    out: Path = ReportsOption,
) -> None:
    """Fetch every source once and print source_id, name, route, HTTP status, items, newest item,
    fields, pass/fail, plus each failure with a suggested fix (BRIEF.md "FIRST RUN"). Stores nothing."""
    sources = _pick(_read_feeds(feeds), source)
    typer.echo(
        f"Testing {len(sources)} sources. Each site gets one request every 2 s, so expect about"
        f" {_estimate_minutes(sources, with_lookups=False)} minutes. Progress:"
    )
    now = datetime.now(timezone.utc)

    async def go():
        async with PoliteClient() as client:
            return await fetch_sources(client, sources, rsshub_base=rsshub_base(), on_done=_progress(len(sources)))

    outcomes = asyncio.run(go())
    names = {s.source_id: s.name for s in sources}
    rows = []
    for o in outcomes:
        h = health.check(o, now)
        rows.append(
            {
                "source_id": o.source_id,
                "name": names[o.source_id],
                "route_type": o.route_type,
                "http_status": o.http_status or "",
                "items": len(o.entries),
                "newest_item_ist": _date(h.newest),
                "fields_present": ",".join(h.fields),
                "result": "PASS" if h.passed else "FAIL",
                "problem": "; ".join(h.problems),
                "suggested_fix": h.fix if not h.passed else "",
                "url": o.url,
            }
        )
    out.mkdir(parents=True, exist_ok=True)
    stamp = now.astimezone(IST).strftime("%Y-%m-%d-%H%M")
    csv_path, md_path = out / f"{stamp}.csv", out / f"{stamp}.md"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["source_id"])
        writer.writeheader()
        writer.writerows(rows)
    md_path.write_text(_markdown(rows, stamp), encoding="utf-8")

    width = {
        "source_id": 6,
        "name": 28,
        "route_type": 26,
        "http_status": 4,
        "items": 5,
        "newest_item_ist": 16,
        "fields_present": 26,
    }
    typer.echo("  ".join(k[:w].ljust(w) for k, w in width.items()) + "  result")
    for r in rows:
        typer.echo("  ".join(str(r[k])[:w].ljust(w) for k, w in width.items()) + "  " + r["result"])
    failed = [r for r in rows if r["result"] == "FAIL"]
    typer.echo(f"\n{len(rows) - len(failed)} passed, {len(failed)} failed.")
    for r in failed:
        typer.echo(f"  {r['source_id']} {r['name']}: {r['problem']}\n      fix: {r['suggested_fix']}")
    typer.echo(f"\nReport: {csv_path} and {md_path}")


def _markdown(rows: list[dict], stamp: str) -> str:
    failed = [r for r in rows if r["result"] == "FAIL"]
    lines = [
        f"# test-feeds, {stamp} IST",
        "",
        f"{len(rows) - len(failed)} passed, {len(failed)} failed, of {len(rows)} sources.",
        "",
        "| Source | Name | Route | HTTP | Items | Newest (IST) | Fields | Result |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for r in rows:
        name = r["name"].replace("|", "/")
        lines.append(
            f"| {r['source_id']} | {name} | {r['route_type']} | {r['http_status']} | {r['items']} |"
            f" {r['newest_item_ist']} | {r['fields_present']} | {r['result']} |"
        )
    if failed:
        lines += ["", "## Failures and suggested fixes", ""]
        for r in failed:
            lines.append(
                f"- **{r['source_id']} {r['name']}** ({r['route_type']}): {r['problem']}. Fix: {r['suggested_fix']}"
            )
    return "\n".join(lines) + "\n"


@app.command("fetch")
def fetch(
    all_sources: bool = AllOption,
    source: list[str] = SourceOption,
    db: Path = DbOption,
    feeds: Path = FeedsOption,
) -> None:
    """One full cycle: fetch, clean, group into stories, tag by keyword, store."""
    if all_sources == bool(source):
        _fail("say either --all, or --source S004 (repeatable), not both")
    conn = connect(db)
    if conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0:
        _read_feeds(feeds)  # a clear message if feeds.csv is missing
        r = import_sources(conn, feeds)
        typer.echo(f"First run: imported {r.added} sources from {feeds}.")
    if source:  # by id, inactive sources included, so a person can still try one by hand
        ids = [w.strip().upper() for w in source]
        sources = store.load_sources(conn, ids)
        unknown = sorted(set(ids) - {s.source_id for s in sources})
        if unknown:
            _fail(f"no such source id: {', '.join(unknown)} (see source_id in feeds.csv)")
    else:
        sources = store.load_sources(conn)
    typer.echo(
        f"Fetching {len(sources)} sources; expect up to about {_estimate_minutes(sources, with_lookups=True)} minutes"
        " (sites are asked politely, one request every 2 s each). Please leave it running. Progress:"
    )
    try:
        with run_lock((Path(db) if db else default_db_path()).parent / "fetch.lock"):
            s = asyncio.run(run_fetch(conn, sources, paths=Paths(), on_progress=_progress(len(sources))))
    except AlreadyRunning as exc:
        _fail(str(exc))
    statuses = ", ".join(f"{n} {k}" for k, n in sorted(s.statuses.items()))
    typer.echo(f"Sources: {statuses}.")
    typer.echo(f"Entries read: {s.entries}; new items stored: {s.items_new}.")
    if s.dropped:
        typer.echo("Dropped: " + ", ".join(f"{n} {reason}" for reason, n in s.dropped.most_common()) + ".")
    typer.echo(
        f"Stories: {s.stories_new} new, {s.stories_grown} grown; {s.tagged} tagged, {s.unmatched_local} unmatched local."
    )
    typer.echo(f"Google News: {s.gnews_online} looked up online, {s.gnews_cached_new} new links cached.")


def main() -> None:
    # never crash on a character the console cannot show (Windows consoles and redirected output use cp1252)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    app()
