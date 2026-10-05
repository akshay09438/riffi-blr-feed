"""Command line (BRIEF.md step 7): python -m riffi_ingest <command>.

    import-sources    seed / refresh sources from feeds.csv (idempotent)
    import-topics     seed / refresh topics from topics.csv (idempotent)
    test-feeds        fetch every source once and report pass / fail with a suggested fix; stores nothing
    fetch             one full cycle (fetch, clean, group, tag, score, store): --all, --source S004 --source S011,
                      or --due (only the sources whose interval is up: what Windows' timer runs every 30 minutes)
    stories           the best stories of the last 24 hours with score, label, sources and topics
    status            is the engine alive: the last run, the last 24 hours, sources failing 3+ runs in a row
    log-template      the editor's empty ground-truth log and the High-priority topics to log from
    check-log         check the editor's ground-truth log for mistakes, row by row
    digest            the day's files in reports/YYYY-MM-DD/: digest.md and digest.csv (ranked stories, topics that
                      moved, quiet High-priority topics) plus the health report; --date regenerates a past day
    report            the health report only: health.md and sources_health.csv

Every command has --help.
"""

from __future__ import annotations

import asyncio
import csv
import sqlite3
import sys
import textwrap
import traceback
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer

from . import groundtruth, health, runstatus
from .config import rsshub_base
from .db import connect, store
from .db.connection import PROJECT_ROOT, default_db_path
from .db.importers import import_sources, import_topics
from .fetchers.feeds import fetch_sources, plan
from .fetchers.gnews import ONLINE_CAP_PER_RUN
from .fetchers.http import DOMAIN_INTERVAL, PoliteClient, domain_key
from .fetchers.parse import IST
from .outputs import queries
from .outputs.digest import CSV_CAP, day_text, write_digest
from .outputs.health_report import write_health
from .pipeline import Paths, RunSummary, run_fetch
from .runlock import AlreadyRunning, run_lock
from .safety.blocklist import load_blocklist
from .scheduler import Schedule, ScheduleError, due
from .scoring import Scorer
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
TopOption = typer.Option(30, "--top", help="How many stories to show.")
HoursOption = typer.Option(24, "--hours", help="Stories updated in this many past hours.")
LabelOption = typer.Option(None, "--label", help="Only High, Medium, Low or Drop.")
DueOption = typer.Option(
    False, "--due", help="Only the sources whose interval is up (config/schedule.yaml): what Windows' timer runs."
)
ScheduleOption = typer.Option(
    PROJECT_ROOT / "config" / "schedule.yaml", "--schedule", help="How often each source is fetched."
)
DayOption = typer.Option(
    None, "--date", help="Regenerate a past day (YYYY-MM-DD): the 24 hours ending 07:00 IST on that date."
)
OutputsOption = typer.Option(PROJECT_ROOT / "reports", "--out", help="Reports go in <out>/YYYY-MM-DD/.")
LogOption = typer.Option(PROJECT_ROOT / "data" / "ground_truth.csv", "--log", help="The editor's ground-truth log.")
LogTopicsOption = typer.Option(
    PROJECT_ROOT / "data" / "ground_truth_topics.csv", "--topic-list", help="Where to write the topics to log from."
)


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


class RunFailed(Exception):
    """A run that started and failed; it is already in the diary and in engine.log."""


def _first_run_imports(conn, feeds: Path, topics: Path) -> None:
    if conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0:
        _read_feeds(feeds)  # a clear message if feeds.csv is missing
        r = import_sources(conn, feeds)
        typer.echo(f"First run: imported {r.added} sources from {feeds}.")
    if conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 0:
        # scoring needs each topic's priority and geography; without them every story would score Drop
        r = import_topics(conn, topics)
        typer.echo(f"First run: imported {r.added} topics from {topics}.")


def _log(path: Path, when: datetime, mode: str, outcome: str, detail: str = "") -> None:
    """One line per run (plus a traceback under a failure) in engine.log beside the database. Windows' timer runs
    the engine with no window, so this file and `status` are how anyone sees what happened. Best-effort: a log
    that cannot be written is skipped, never allowed to break the run."""
    line = f"{when.astimezone(IST):%Y-%m-%d %H:%M} IST  {mode:<6} {outcome:<11} {detail}".rstrip()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # the diary row is the other trace; a log that cannot be written must not lose it


def _trace() -> str:
    return textwrap.indent(traceback.format_exc().rstrip(), "    ")


def _reason(exc: BaseException) -> str:
    """'RuntimeError: disk full', or just 'KeyboardInterrupt' when the error has no message."""
    return (f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)[:500]


def _note_failure(conn, log: Path, now: datetime, mode: str, exc: Exception, sources_due: int = 0) -> None:
    """A run that could not start still leaves a trace: a line in engine.log and, if the database answers, a
    'failed' row in the diary, so `status` does not mistake it for the laptop being off."""
    reason = _reason(exc)
    _log(log, now, mode, "failed", f"{reason}\n{_trace()}")
    if conn is not None:
        try:
            store.log_engine_tick(conn, now, mode, "failed", sources_due, error=reason)
        except sqlite3.Error:
            pass  # the database itself is the problem (still locked, say): the log line above is the trace


def _tick(conn, log: Path, now: datetime, mode: str, outcome: str, sources_due: int = 0) -> None:
    """A check that fetched nothing ('nothing_due', or 'busy' while another run held the lock). The log line goes
    first, so a database that will not answer still leaves a trace."""
    _log(log, now, mode, outcome, f"sources {sources_due}" if sources_due else "")
    try:
        store.log_engine_tick(conn, now, mode, outcome, sources_due)
    except sqlite3.Error as exc:
        _log(log, now, mode, "warning", f"could not write the diary row: {_reason(exc)}")


def _pick_for_run(conn, mode: str, wanted: list[str], schedule: Path, now: datetime, log: Path) -> list[Source]:
    if mode == "source":  # by id, inactive sources included, so a person can still try one by hand
        ids = [w.strip().upper() for w in wanted]
        sources = store.load_sources(conn, ids)
        unknown = sorted(set(ids) - {s.source_id for s in sources})
        if unknown:
            _fail(f"no such source id: {', '.join(unknown)} (see source_id in feeds.csv)")
        return sources
    active = store.load_sources(conn)
    if mode == "all":
        return active
    speeds = Schedule.load(schedule)
    for problem in speeds.problems(active):
        typer.echo(f"Warning: {problem} in {schedule}; those sources are never fetched.")
        _log(log, now, mode, "warning", problem)
    return due(active, store.last_attempted(conn, now), speeds, now)


def _run_and_record(conn, sources: list[Source], mode: str, now: datetime, log: Path) -> RunSummary:
    """One run between a 'running' diary row and its end. The row is always closed, Ctrl+C included; only a hard
    kill leaves it 'running', which `status` then reports as 'did not finish'."""
    run_id = store.start_engine_run(conn, now, mode, len(sources))
    try:
        s = asyncio.run(run_fetch(conn, sources, paths=Paths(), now=now, on_progress=_progress(len(sources))))
    except BaseException as exc:
        reason = _reason(exc)
        _log(log, now, mode, "failed", f"sources {len(sources)}, {reason}\n{_trace()}")
        try:
            store.finish_engine_run(conn, run_id, datetime.now(timezone.utc), "failed", error=reason)
        except sqlite3.Error as db_exc:  # the log line above is the trace; `status` will say 'did not finish'
            _log(log, now, mode, "warning", f"could not close the diary row: {_reason(db_exc)}")
        if isinstance(exc, Exception):
            raise RunFailed(reason) from exc
        raise
    ok = s.statuses["ok"] + s.statuses["not_modified"]
    failed, skipped = s.statuses["error"], s.statuses["skipped"]
    finished = datetime.now(timezone.utc)
    outcome = "offline" if s.offline else "ok"
    _log(
        log,
        now,
        mode,
        outcome,
        f"sources {len(sources)}, ok {ok}, failed {failed}, skipped {skipped}, new items {s.items_new},"
        f" google refusals {s.google_refusals}, {(finished - now).total_seconds() / 60:.1f} min",
    )
    try:
        store.finish_engine_run(
            conn,
            run_id,
            finished,
            outcome,
            sources_ok=ok,
            sources_failed=failed,
            sources_skipped=skipped,
            items_new=s.items_new,
            google_refusals=s.google_refusals,
        )
    except sqlite3.Error as exc:  # the run itself worked and the log says so; the diary row stays 'running'
        _log(log, now, mode, "warning", f"could not close the diary row: {_reason(exc)}")
    return s


@app.command("fetch")
def fetch(
    all_sources: bool = AllOption,
    source: list[str] = SourceOption,
    due_only: bool = DueOption,
    db: Path = DbOption,
    feeds: Path = FeedsOption,
    topics: Path = TopicsOption,
    schedule: Path = ScheduleOption,
) -> None:
    """One full cycle: fetch, clean, group into stories, tag by keyword, score, store. --due fetches only the
    sources whose interval is up (what Windows' timer runs every 30 minutes). Every run goes into the run diary
    (see `status`) and one line into engine.log beside the database."""
    if [all_sources, bool(source), due_only].count(True) != 1:
        _fail("say exactly one of --all, --source S004 (repeatable) or --due")
    db_path = Path(db) if db else default_db_path()
    log = db_path.parent / "engine.log"
    mode = "due" if due_only else "all" if all_sources else "source"
    now = datetime.now(timezone.utc)
    conn = None
    try:
        conn = connect(db_path)
        _first_run_imports(conn, feeds, topics)
        sources = _pick_for_run(conn, mode, source, schedule, now, log)
    except typer.Exit:
        raise
    except Exception as exc:  # the timer runs with no window: a run that cannot start must still leave a trace
        _note_failure(conn, log, now, mode, exc)
        _fail(f"could not start: {exc} (details in {log})")
    if not sources:
        _tick(conn, log, now, mode, "nothing_due")
        typer.echo("Nothing is due yet.")
        return
    typer.echo(
        f"Fetching {len(sources)} sources; expect up to about {_estimate_minutes(sources, with_lookups=True)} minutes"
        " (sites are asked politely, one request every 2 s each). Please leave it running. Progress:"
    )
    try:
        with run_lock(db_path.parent / "fetch.lock"):
            s = _run_and_record(conn, sources, mode, now, log)
    except AlreadyRunning as exc:
        if mode != "due":
            _fail(str(exc))
        _tick(conn, log, now, mode, "busy", len(sources))
        typer.echo("Another fetch is still running; this check is skipped.")
        return
    except RunFailed as exc:
        _fail(f"the run failed: {exc} (details in {log})")
    except Exception as exc:  # e.g. the database stayed locked for 30 s before the run could start
        _note_failure(conn, log, now, mode, exc, len(sources))
        _fail(f"could not start: {exc} (details in {log})")
    statuses = ", ".join(f"{n} {k}" for k, n in sorted(s.statuses.items()))
    typer.echo(f"Sources: {statuses}.")
    if s.offline:
        typer.echo(
            "No internet: none of the sites could be reached. Nothing is counted against the sources;"
            " they are tried again next time."
        )
        return
    if s.google_refusals:
        typer.echo(f"Warning: Google News refused {s.google_refusals} requests (403 / 429). Check `status`.")
    typer.echo(
        f"Entries read: {s.entries}; new items stored: {s.items_new}; already stored: {s.already_stored};"
        f" repeated within this run: {s.repeated}."
    )
    if s.dropped:
        typer.echo("Dropped: " + ", ".join(f"{n} {reason}" for reason, n in s.dropped.most_common()) + ".")
    typer.echo(
        f"Stories: {s.stories_new} new, {s.stories_grown} grown; {s.tagged} tagged, {s.unmatched_local} unmatched local."
    )
    typer.echo(f"Google News: {s.gnews_online} looked up online, {s.gnews_cached_new} new links cached.")
    if s.labels:
        typer.echo(
            "Scored: " + ", ".join(f"{s.labels[k]} {k}" for k in ("High", "Medium", "Low", "Drop") if s.labels[k]) + "."
        )
    typer.echo("See the best ones with: python -m riffi_ingest stories")


@app.command("stories")
def stories(
    top: int = TopOption,
    hours: int = HoursOption,
    label: str = LabelOption,
    db: Path = DbOption,
) -> None:
    """The best stories updated in the last --hours (default 24), highest score first (BRIEF.md "FIRST RUN",
    point 3). Score and label exclude the AI points until the AI pass runs ("awaiting AI")."""
    conn = connect(db)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = store.top_stories(conn, since, top, label.capitalize() if label else None)
    if not rows:
        typer.echo(f"No stories in the last {hours} hours. Run: python -m riffi_ingest fetch --all")
        return
    typer.echo(f"Top {len(rows)} stories of the last {hours} hours (score / label / sources / topics / headline):")
    for n, r in enumerate(rows, 1):
        flags = (" [sensitive]" if r["sensitive"] else "") + ("" if r["ai_checked"] else " [awaiting AI]")
        score = "" if r["relevance_score"] is None else f"{r['relevance_score']:.0f}"
        typer.echo(
            f"{n:>3}. {score:>3} {r['label'] or '-':<6} {r['source_count']:>2} src  {(r['topics'] or '-')[:24]:<24}"
            f"  {r['headline'][:90]}{flags}"
        )


@app.command("status")
def status(db: Path = DbOption, schedule: Path = ScheduleOption) -> None:
    """Is the engine alive? The last run, the last 24 hours (checks, gaps, Google refusals) and the sources
    failing 3+ runs in a row. Changes nothing."""
    db_path = Path(db) if db else default_db_path()
    conn = connect(db_path)
    for line in _engine_lines(conn, db_path, schedule, datetime.now(timezone.utc)):
        typer.echo(line)


def _engine_lines(conn, db_path: Path, schedule: Path, now: datetime) -> list[str]:
    """What `status` prints: the run diary's story plus any problem with the speeds. Shared with the health report."""
    try:  # a fetch holding the run lock right now is the only run that can still be going
        with run_lock(db_path.parent / "fetch.lock"):
            run_in_progress = False
    except AlreadyRunning:
        run_in_progress = True
    try:
        speeds, problem = Schedule.load(schedule), None
    except (OSError, ScheduleError) as exc:
        speeds, problem = None, exc
    timer = speeds.timer if speeds else True
    lines = runstatus.report(conn, now, run_in_progress=run_in_progress, timer=timer)
    if speeds:
        lines += [
            f"Warning: {p} in {schedule}; those sources are never fetched."
            for p in speeds.problems(store.load_sources(conn))
        ]
    else:
        lines.append(f"Warning: cannot read the speeds ({problem}); timed runs fail until it is fixed.")
    return lines


@app.command("log-template")
def log_template(log: Path = LogOption, topic_list: Path = LogTopicsOption, topics: Path = TopicsOption) -> None:
    """Create the editor's empty ground-truth log (never overwrites one that exists) and the list of
    High-priority topics to log from (BRIEF.md "Two-week test")."""
    known = groundtruth.load_topics(topics)
    created = groundtruth.write_template(log, topic_list, known)
    high = sum(t.priority == "High" for t in known.values())
    typer.echo(f"Log: {'created' if created else 'already there, left as it is:'} {log}")
    typer.echo(f"Topics to log from: {high} High-priority topics in {topic_list}")
    typer.echo("Each day: one row per real development. Then run: python -m riffi_ingest check-log")


@app.command("check-log")
def check_log(log: Path = LogOption, topics: Path = TopicsOption) -> None:
    """Check the editor's ground-truth log row by row: dates, times, topic ids, empty cells, repeats.
    Changes nothing. Exits 1 when a row must be fixed before the recall test can score it."""
    if not log.exists():
        _fail(f"cannot find {log}. Create it with: python -m riffi_ingest log-template")
    check = groundtruth.read_log(log, groundtruth.load_topics(topics), datetime.now(timezone.utc))
    for line in check.errors:
        typer.echo(f"Fix: {line}")
    for line in check.warnings:
        typer.echo(f"Note: {line}")
    days = len({e.seen_at.date() for e in check.entries})
    typer.echo(
        f"{len(check.entries)} developments over {days} days are ready to score; {len(check.errors)} problem(s) to fix."
    )
    if check.errors:
        raise typer.Exit(code=1)


def _report_day(text: str | None, now: datetime):
    """--date as a date, refused when it is not YYYY-MM-DD or lies after today (IST)."""
    if text is None:
        return None
    try:
        day = datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        _fail(f"--date {text!r} is not a date: write it as YYYY-MM-DD, e.g. 2026-10-06")
    if day > now.astimezone(IST).date():
        _fail(f"--date {text} is in the future: give today or a past day")
    return day


def _open_existing(db: Path | None) -> tuple[Path, sqlite3.Connection]:
    """The reports only read: a missing database is an error, never quietly created empty."""
    db_path = Path(db) if db else default_db_path()
    if not db_path.exists():
        _fail(f"there is no database at {db_path} yet. Fetch first: python -m riffi_ingest fetch --all")
    return db_path, connect(db_path)


def _write_health_files(conn, db_path: Path, schedule: Path, folder: Path, now: datetime, day) -> None:
    try:
        problems = load_blocklist(Paths().blocklist).problems
    except OSError as exc:
        problems = [f"cannot read blocklist.csv ({exc}): nothing is being blocked"]
    h = write_health(
        conn,
        folder,
        now=now,
        day=day,
        engine_lines=_engine_lines(conn, db_path, schedule, now),
        blocklist_problems=problems,
    )
    flagged = h.categories["failing"] + h.not_useful
    typer.echo(
        f"Health: {h.categories['working']} sources working, {h.categories['failing']} failing 3+ in a row,"
        f" {h.categories['stale']} stale, {h.not_useful} not useful in 7 days ({flagged} flagged in all)."
    )
    typer.echo(f"  {h.md_path}\n  {h.csv_path}")


@app.command("digest")
def digest(
    date: str = DayOption,
    out: Path = OutputsOption,
    top: int = TopOption,
    db: Path = DbOption,
    schedule: Path = ScheduleOption,
) -> None:
    """The daily files (BRIEF.md step 8, D-012) in <out>/YYYY-MM-DD/: digest.md and digest.csv (the stories of the
    24 hours before now, best first; the topics that moved; High-priority topics quiet for 7+ days) plus
    health.md and sources_health.csv. --date YYYY-MM-DD regenerates a past day (the 24 hours ending 07:00 IST).
    A rerun overwrites. Reads the database, never changes it."""
    now = datetime.now(timezone.utc)
    day = _report_day(date, now)
    if not 1 <= top <= CSV_CAP:
        _fail(f"--top must be between 1 and {CSV_CAP}")
    db_path, conn = _open_existing(db)
    if not queries.has_items(conn):
        _fail(
            "the database has no articles yet, so there is nothing to report. Run: python -m riffi_ingest fetch --all"
        )
    w = queries.window_for(now, day)
    folder = Path(out) / w.day.isoformat()
    d = write_digest(conn, w, folder, top=top, scorer=Scorer.load(Paths().scoring))
    c = d.counts
    typer.echo(
        f"Digest for {day_text(w.day)}: {c.items} items fetched, {c.stories} stories ({c.new_stories} new);"
        f" {c.labels.get('High', 0)} High, {c.labels.get('Medium', 0)} Medium, {c.labels.get('Low', 0)} Low,"
        f" {c.labels.get('Drop', 0)} Drop; {d.listed} listed in the CSV."
    )
    if c.items == 0:
        typer.echo("No articles were fetched in this window. Run a fetch first: python -m riffi_ingest fetch --all")
    typer.echo(f"  {d.md_path}\n  {d.csv_path}")
    _write_health_files(conn, db_path, schedule, folder, now, w.day)


@app.command("report")
def report(
    date: str = DayOption,
    out: Path = OutputsOption,
    db: Path = DbOption,
    schedule: Path = ScheduleOption,
) -> None:
    """The feed health report only (BRIEF.md steps 8-9): health.md (engine status; sources working, failing,
    stale, not useful, skipped; blocklist problems) and sources_health.csv (the four columns to paste into the
    source sheet), in <out>/YYYY-MM-DD/. Health is always as of now. Reads the database, never changes it."""
    now = datetime.now(timezone.utc)
    day = _report_day(date, now) or now.astimezone(IST).date()
    db_path, conn = _open_existing(db)
    if conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0:
        _fail("the database has no sources yet. Run: python -m riffi_ingest import-sources (or fetch --all)")
    _write_health_files(conn, db_path, schedule, Path(out) / day.isoformat(), now, day)


def main() -> None:
    # never crash on a character the console cannot show (Windows consoles and redirected output use cp1252)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    app()
