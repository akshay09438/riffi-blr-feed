from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from riffi_ingest import cli, runstatus
from riffi_ingest.db import connect
from riffi_ingest.db.importers import import_sources
from riffi_ingest.db.store import finish_engine_run, log_engine_tick, record_fetch, start_engine_run
from riffi_ingest.fetchers.outcome import FetchOutcome
from riffi_ingest.runlock import run_lock

NOW = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
H = timedelta(hours=1)
M = timedelta(minutes=1)


@pytest.fixture
def db(tmp_path, repo_root):
    conn = connect(tmp_path / "engine.db")
    import_sources(conn, repo_root / "feeds.csv", now=NOW)
    yield conn
    conn.close()


def a_run(conn, started, outcome="ok", minutes=8, ok=110, failed=6, refusals=0, error=None):
    rid = start_engine_run(conn, started, "due", 120)
    if outcome != "running":
        finish_engine_run(
            conn,
            rid,
            started + timedelta(minutes=minutes),
            outcome,
            sources_ok=ok,
            sources_failed=failed,
            sources_skipped=4,
            items_new=240,
            google_refusals=refusals,
            error=error,
        )
    return rid


def ticks(conn, start, end):
    """Timer checks with nothing due, every 30 minutes from `start` to `end`."""
    at = start
    while at <= end:
        log_engine_tick(conn, at, "due", "nothing_due")
        at += 30 * M


def text(conn, run_in_progress=False):
    return "\n".join(runstatus.report(conn, NOW, run_in_progress=run_in_progress))


def test_before_any_run(db):
    assert "No runs in the run diary yet" in text(db)


def test_a_healthy_day(db):
    ticks(db, NOW - 23 * H - 30 * M, NOW - 30 * M)
    a_run(db, NOW - 10 * M)
    out = text(db)
    assert "Last run: 2026-10-10 11:20 IST (10 min ago), the sources that were due: done in 8 min." in out
    assert "120 sources: 110 worked, 6 failed, 4 skipped; 240 new articles." in out
    assert "Last 24 hours: 48 checks - 47 nothing due, 1 ran." in out
    assert "No gaps" in out and "Google refusals: 0." in out and "future" not in out
    assert "Sources failing 3+ runs in a row: none." in out


def test_a_gap_is_shown_with_its_times(db):
    ticks(db, NOW - 20 * H, NOW - 12 * H)
    ticks(db, NOW - 6 * H, NOW)
    a_run(db, NOW - 6 * H)
    assert "Longest gap with no check: 6.0 h, 2026-10-09 23:30 IST to 2026-10-10 05:30 IST" in text(db)


def test_an_outage_that_began_before_the_window_still_counts(db):
    ticks(db, NOW - 30 * H, NOW - 25 * H)
    ticks(db, NOW - 2 * H, NOW)
    a_run(db, NOW - 2 * H)
    assert "Longest gap with no check: 22.0 h, 2026-10-09 11:30 IST to 2026-10-10 09:30 IST" in text(db)


def test_a_long_run_is_not_a_gap(db):
    ticks(db, NOW - 23 * H, NOW - 90 * M)
    a_run(db, NOW - 60 * M, minutes=50)  # Windows skipped the tick due while it ran
    ticks(db, NOW, NOW)
    assert "No gaps" in text(db)


def test_a_run_that_never_finished(db):
    a_run(db, NOW - 3 * H, outcome="running")
    ticks(db, NOW - 2 * H - 30 * M, NOW)
    out = text(db)
    assert "did not finish (stopped or crashed)" in out and "1 did not finish" in out


def test_a_run_holding_the_lock_is_running_and_a_dead_one_is_not(db):
    a_run(db, NOW - 3 * M, outcome="running")
    assert ": running now." in text(db, run_in_progress=True)
    assert "did not finish (stopped or crashed)" in text(db, run_in_progress=False)  # killed 3 minutes in


def test_failed_and_offline_runs_are_explained(db):
    a_run(db, NOW - 40 * M, outcome="failed", minutes=1, error="RuntimeError: disk full")
    assert "FAILED after 1 min - RuntimeError: disk full" in text(db)
    a_run(db, NOW - 10 * M, outcome="offline", minutes=1, ok=0, failed=116)
    out = text(db)
    assert "no internet - none of the 120 sources could be reached" in out
    assert "1 failed" in out and "1 offline (no internet)" in out


def test_a_check_that_could_not_start_is_shown_as_failed(db):
    log_engine_tick(db, NOW - 5 * M, "due", "failed", error="ScheduleError: not a speed: 'fortnightly'")
    assert "FAILED after under a minute - ScheduleError: not a speed: 'fortnightly'" in text(db)


def test_google_refusals_raise_a_warning(db):
    a_run(db, NOW - 10 * M, refusals=3)
    assert "Google refusals: 3 - WARNING" in text(db)


def test_rows_dated_in_the_future_raise_a_clock_warning(db):
    a_run(db, NOW - 10 * M)
    log_engine_tick(db, NOW + 24 * H, "due", "nothing_due")
    assert "dated in the future" in text(db)


def test_sources_failing_three_runs_in_a_row_are_listed(db):
    for n in range(3):
        outcome = FetchOutcome("S057", "Native publisher RSS/Atom", "error", reason="HTTP 404")
        record_fetch(db, outcome, NOW - n * H)
    a_run(db, NOW - 10 * M)
    out = text(db)
    assert "Sources failing 3+ runs in a row (1;" in out
    assert "  S057 Vijaya Karnataka - 3 in a row - error: HTTP 404" in out


def test_a_day_with_no_checks_says_so(db):
    a_run(db, NOW - 48 * H)
    out = text(db)
    assert "(2.0 days ago)" in out and "no checks at all" in out


def test_the_status_command(tmp_path):
    db_path = tmp_path / "e.db"
    r = CliRunner().invoke(cli.app, ["status", "--db", str(db_path)])
    assert r.exit_code == 0 and "No runs in the run diary yet" in r.output
    conn = connect(db_path)
    start_engine_run(conn, datetime.now(timezone.utc) - 2 * M, "due", 3)
    with run_lock(tmp_path / "fetch.lock"):  # a fetch is running right now
        busy = CliRunner().invoke(cli.app, ["status", "--db", str(db_path)])
    assert busy.exit_code == 0 and "running now" in busy.output
    dead = CliRunner().invoke(cli.app, ["status", "--db", str(db_path)])
    assert "did not finish" in dead.output


def test_fetching_by_hand_makes_gaps_expected_not_alarming(db):
    a_run(db, NOW - 48 * H)
    out = "\n".join(runstatus.report(db, NOW, timer=False))
    assert "Last 24 hours: no fetches - fetching is by hand for now, D-009." in out and "laptop" not in out
    a_run(db, NOW - 20 * H)
    out = "\n".join(runstatus.report(db, NOW, timer=False))
    assert "Last 24 hours: 1 check - 1 ran." in out
    assert "Longest gap between fetches: 19.9 h" in out and "(fetching is by hand for now, D-009)." in out


def test_a_diary_of_only_quiet_checks_is_not_called_empty(db):
    ticks(db, NOW - 2 * H, NOW - 30 * M)
    out = text(db)
    assert "No fetch has run yet: every check so far found nothing due." in out and "diary yet" not in out
