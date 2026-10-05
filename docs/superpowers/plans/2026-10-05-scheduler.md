# Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The engine fetches on its own: Windows' timer starts `fetch --due` every 30 minutes, each source is fetched at its own speed, every run and every gap is recorded in a run diary, and `status` shows whether it is alive.

**Architecture:** `config/schedule.yaml` gives each route type (and optionally each source) a speed. `scheduler.due()` picks the sources whose interval is up, counted from their last `fetch_runs.started_at`. `cli fetch --due` runs the existing pipeline on them under the existing run lock, and writes a run diary row (`engine_runs`) and a log line. `pipeline.run_fetch` recognises a run with no internet and stores nothing for it. `runstatus.report()` turns the diary into the `status` text. `scripts/schedule-windows.ps1` registers the Windows task.

**Tech Stack:** Python 3.11, SQLite, Typer, PyYAML, pytest; Windows Task Scheduler (PowerShell `Register-ScheduledTask`). No new libraries.

Spec: `docs/superpowers/specs/2026-10-05-scheduler-design.md`.

## Global Constraints

- No new dependencies: `requirements.txt` is not touched.
- `riffi_ingest/fetchers/http.py` and `riffi_ingest/fetchers/gnews.py` are read-only for this work (dangerous list item 8).
- Dangerous files in this plan: `riffi_ingest/db/schema.py` and `riffi_ingest/db/store.py` (Task 3), and `CLAUDE.md` and `.zuko/config.json` (Task 7). They are edited only after the founder's explicit yes and `node .zuko/approve.js --files "<exact paths>" --reason "..." --ack "<founder's words>"`; clear afterwards with `node .zuko/approve.js --clear`.
- Dates are stored as ISO-8601 UTC through `db.connection.utc_iso` and shown in IST (`fetchers.parse.IST`).
- Speeds during the two-week test (D-008): Telegram 30m; Google News, X/Instagram backups, publisher feeds and YouTube 2h; page monitors 6h; Manual never; `early_minutes: 5`.
- The timer beat is 30 minutes. A run is stopped by Windows after 1 hour.
- Line length 120 (`ruff.toml`); comments in the codebase's plain-English style.
- Checks: `node .claude/hooks/py.js -m ruff check .`, `node .claude/hooks/py.js -m ruff format --check .`, `node .claude/hooks/py.js -m pytest -q tests`.
- Branch `feat/scheduler`; never commit to `main`. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: Speeds and the due rule

User job: decides *which* sources the two-week test fetches on each 30-minute tick, so every source is fetched at its agreed speed and nothing piles up after a sleep.

**Files:**
- Create: `config/schedule.yaml`
- Create: `riffi_ingest/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `riffi_ingest.sources.Source` (frozen dataclass: `source_id`, `name`, `category`, `route_type`, `fetch_url`, ...), `load_sources(path)`.
- Produces: `ScheduleError(ValueError)`; `parse_interval(text) -> timedelta | None`; `Schedule(by_route: dict[str, timedelta | None], overrides: dict[str, timedelta | None] = {}, early: timedelta = 5 min)` with `Schedule.load(path) -> Schedule`, `.interval(source) -> timedelta | None`, `.problems(sources) -> list[str]`; `due(sources, last_attempted: dict[str, datetime], schedule, now) -> list[Source]`.

- [ ] **Step 1: Write the failing tests** - `tests/test_scheduler.py`

```python
from datetime import datetime, timedelta, timezone

import pytest

from riffi_ingest.scheduler import Schedule, ScheduleError, due, parse_interval
from riffi_ingest.sources import Source, load_sources

NOW = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)
SPEEDS = {
    "Google News RSS": timedelta(hours=2),
    "RSSHub Telegram": timedelta(minutes=30),
    "Web page monitor": timedelta(hours=6),
    "Manual": None,
}
SCHEDULE = Schedule(by_route=SPEEDS, early=timedelta(minutes=5))


def src(source_id, route_type):
    return Source(source_id=source_id, name=source_id, category="", route_type=route_type, fetch_url="https://a.in/f")


GN, TG, PAGE = src("S001", "Google News RSS"), src("S002", "RSSHub Telegram"), src("S003", "Web page monitor")


def test_speeds_are_minutes_hours_or_never():
    assert parse_interval("30m") == timedelta(minutes=30)
    assert parse_interval("2h") == timedelta(hours=2)
    assert parse_interval(" 6H ") == timedelta(hours=6)
    assert parse_interval("never") is None
    for bad in ("", "2 hours", "0m", "-1h", "90s", "1.5h", "120"):
        with pytest.raises(ScheduleError):
            parse_interval(bad)


def test_a_source_never_tried_is_due():
    assert due([GN, TG, PAGE], {}, SCHEDULE, NOW) == [GN, TG, PAGE]


def test_each_route_type_has_its_own_speed():
    def last(ago):
        return {s.source_id: NOW - ago for s in (GN, TG, PAGE)}

    assert due([GN, TG, PAGE], last(timedelta(minutes=31)), SCHEDULE, NOW) == [TG]
    assert due([GN, TG, PAGE], last(timedelta(hours=2)), SCHEDULE, NOW) == [GN, TG]
    assert due([GN, TG, PAGE], last(timedelta(hours=6)), SCHEDULE, NOW) == [GN, TG, PAGE]


def test_early_minutes_absorb_timer_jitter():
    # the last run's clock started a few seconds after its tick; the next tick comes 30 minutes after that tick
    assert due([TG], {"S002": NOW - timedelta(minutes=29, seconds=55)}, SCHEDULE, NOW) == [TG]
    assert due([TG], {"S002": NOW - timedelta(minutes=25)}, SCHEDULE, NOW) == [TG]  # exactly interval - early
    assert due([TG], {"S002": NOW - timedelta(minutes=24, seconds=59)}, SCHEDULE, NOW) == []


def test_manual_and_route_types_without_a_speed_are_never_due():
    assert due([src("S100", "Manual"), src("S101", "Carrier pigeon")], {}, SCHEDULE, NOW) == []


def test_a_per_source_speed_beats_the_route_speed():
    faster = Schedule(by_route=SPEEDS, overrides={"S001": timedelta(minutes=30)}, early=timedelta(minutes=5))
    assert due([GN], {"S001": NOW - timedelta(minutes=30)}, faster, NOW) == [GN]
    off = Schedule(by_route=SPEEDS, overrides={"S001": None})
    assert due([GN], {}, off, NOW) == []


def test_load_reads_the_file_and_names_a_bad_value(tmp_path):
    path = tmp_path / "schedule.yaml"
    path.write_text(
        "early_minutes: 3\nby_route:\n  Google News RSS: 2h\n  Manual: never\nsources:\n  s016: 30m\n", encoding="utf-8"
    )
    loaded = Schedule.load(path)
    assert loaded.early == timedelta(minutes=3)
    assert loaded.by_route == {"Google News RSS": timedelta(hours=2), "Manual": None}
    assert loaded.overrides == {"S016": timedelta(minutes=30)}
    path.write_text("by_route:\n  Google News RSS: fortnightly\n", encoding="utf-8")
    with pytest.raises(ScheduleError, match="fortnightly"):
        Schedule.load(path)
    path.write_text("by_route:\n  Manual: never\nsources:\n  # S016: 30m\n", encoding="utf-8")
    assert Schedule.load(path).overrides == {}  # an overrides block holding only comments is empty


def test_problems_lists_route_types_without_a_speed_and_unknown_overrides():
    schedule = Schedule(by_route={"Google News RSS": timedelta(hours=2)}, overrides={"S999": None})
    assert schedule.problems([GN, src("S004", "YouTube Atom")]) == [
        "no speed for route type 'YouTube Atom'",
        "a speed for unknown or inactive source S999",
    ]


def test_the_real_schedule_covers_every_real_source(repo_root):
    schedule = Schedule.load(repo_root / "config" / "schedule.yaml")
    sources = load_sources(repo_root / "feeds.csv")
    assert schedule.problems(sources) == []
    speeds = {s.route_type: schedule.interval(s) for s in sources}
    assert speeds == {  # D-008: the two-week test's speeds
        "Google News RSS": timedelta(hours=2),
        "X/Instagram via RSS.app": timedelta(hours=2),
        "Native publisher RSS/Atom": timedelta(hours=2),
        "YouTube Atom": timedelta(hours=2),
        "RSSHub Telegram": timedelta(minutes=30),
        "Web page monitor": timedelta(hours=6),
        "Manual": None,
    }
    assert schedule.early == timedelta(minutes=5)
```

- [ ] **Step 2: Run them to see them fail**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_scheduler.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'riffi_ingest.scheduler'`.

- [ ] **Step 3: Write `riffi_ingest/scheduler.py`**

```python
"""Step 7 (BRIEF.md "STEP 7: SCHEDULE", D-008): which sources are due for a fetch.

Windows Task Scheduler starts `python -m riffi_ingest fetch --due` every 30 minutes
(scripts/schedule-windows.ps1). Each time, only the sources whose interval is up are fetched, counted from
their last attempt (the latest fetch_runs.started_at, whatever its status). After the laptop has slept or been
off, the next tick finds every overdue source due once: one catch-up run, never a pile-up.

How often each source is fetched lives in config/schedule.yaml (team-editable): a speed per route type, plus
per-source overrides. A route type with no speed is never fetched; `problems` names it so a person sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .sources import Source

_SPEED = re.compile(r"(\d+)([mh])")


class ScheduleError(ValueError):
    pass


def parse_interval(text: object) -> timedelta | None:
    """'30m' -> 30 minutes, '2h' -> 2 hours, 'never' -> None."""
    value = str(text).strip().lower()
    if value == "never":
        return None
    m = _SPEED.fullmatch(value)
    if not m or int(m.group(1)) == 0:
        raise ScheduleError(f"not a speed: {text!r} (use e.g. 30m, 2h or never)")
    n = int(m.group(1))
    return timedelta(minutes=n) if m.group(2) == "m" else timedelta(hours=n)


@dataclass
class Schedule:
    by_route: dict[str, timedelta | None]
    overrides: dict[str, timedelta | None] = field(default_factory=dict)  # source_id (upper case) -> speed
    early: timedelta = timedelta(minutes=5)  # due this much before the interval is up, so timer jitter costs nothing

    @classmethod
    def load(cls, path: str | Path) -> Schedule:
        try:
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            return cls(
                by_route={str(k).strip(): parse_interval(v) for k, v in (raw.get("by_route") or {}).items()},
                overrides={str(k).strip().upper(): parse_interval(v) for k, v in (raw.get("sources") or {}).items()},
                early=timedelta(minutes=int(raw.get("early_minutes", 5))),
            )
        except (ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
            raise ScheduleError(f"{path}: {exc}") from exc

    def interval(self, source: Source) -> timedelta | None:
        """How often `source` is fetched; None means never."""
        source_id = source.source_id.upper()
        if source_id in self.overrides:
            return self.overrides[source_id]
        return self.by_route.get(source.route_type)

    def problems(self, sources: list[Source]) -> list[str]:
        """Route types with no speed (their sources would never be fetched) and overrides naming no source."""
        routes = sorted({s.route_type for s in sources} - set(self.by_route))
        unknown = sorted(set(self.overrides) - {s.source_id.upper() for s in sources})
        return [f"no speed for route type {r!r}" for r in routes] + [
            f"a speed for unknown or inactive source {u}" for u in unknown
        ]


def due(sources: list[Source], last_attempted: dict[str, datetime], schedule: Schedule, now: datetime) -> list[Source]:
    """The sources to fetch now: never tried, or last tried at least (interval - early) ago."""
    out = []
    for source in sources:
        every = schedule.interval(source)
        if every is None:
            continue
        last = last_attempted.get(source.source_id)
        if last is None or now - last >= every - schedule.early:
            out.append(source)
    return out
```

- [ ] **Step 4: Write `config/schedule.yaml`**

```yaml
# How often each source is fetched (step 7, D-008). Team-editable; read on every run.
#
# Windows' timer starts `python -m riffi_ingest fetch --due` every 30 minutes (scripts/schedule-windows.ps1).
# Each time, the engine fetches the sources whose interval is up, counted from their last attempt.
# A speed is <minutes>m, <hours>h or never. Anything faster than 30m behaves like 30m (the timer's beat).
#
# Two-week test (D-008): Google News every 2 h, not the brief's 30 min. Fewer requests to Google lower the risk
# of a block (D-004), and 2 h already meets the 24 h recall target. Before launch, a feed that broke news first
# during the test can be sped up under `sources:` below.

early_minutes: 5            # a source counts as due this many minutes early, so timer jitter never costs a whole beat

by_route:                   # feeds.csv route_type -> how often
  Google News RSS: 2h
  X/Instagram via RSS.app: 2h      # phase 1 reads each row's backup Google News query; X is never fetched
  Native publisher RSS/Atom: 2h
  YouTube Atom: 2h
  RSSHub Telegram: 30m
  Web page monitor: 6h
  Manual: never

sources:                    # per-source speeds that beat the route's, e.g.
  # S016: 30m
  # S041: never
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_scheduler.py`
Expected: 9 passed.

- [ ] **Step 6: Commit**

```bash
git add config/schedule.yaml riffi_ingest/scheduler.py tests/test_scheduler.py
git commit -m "Scheduler: speeds per route type and the due rule (step 7)"
```

---

### Task 2: A run with no internet blames no source; Google refusals are counted

User job: keeps the two-week test fair. A night with the Wi-Fi off must not flag 131 sources for replacement. If Google starts refusing us, the founder must see it straight away (D-004).

**Files:**
- Modify: `riffi_ingest/pipeline.py` (imports, `RunSummary`, `run_fetch` lines 84-97, two new functions)
- Test: `tests/test_pipeline_cli.py` (append)

**Interfaces:**
- Consumes: `fetchers.http.NETWORK_KINDS` (`{"timeout", "dns", "connect"}`), `fetchers.http.domain_key(url)`, `FetchOutcome` (`status`, `error_kind`, `url`, `http_status`).
- Produces: `pipeline.looks_offline(outcomes) -> bool`, `pipeline.google_refusals(outcomes) -> int`, `RunSummary.offline: bool`, `RunSummary.google_refusals: int`; `run_fetch(...)` returns early with `offline=True` and stores nothing per source.

- [ ] **Step 1: Write the failing tests** - append to `tests/test_pipeline_cli.py`

```python
# ---- no internet, and Google saying no


def unreachable(request):
    raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request)


def test_no_internet_blames_no_source(db):
    s = run(db, ["S011", gn_source_id(db), "S112"], unreachable)
    assert s.offline and s.statuses == {"error": 2, "skipped": 1}
    assert db.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 0  # so every source is still due next tick
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    assert db.execute("SELECT MAX(consecutive_failures) FROM sources").fetchone()[0] == 0


def test_one_unreachable_site_is_that_sites_problem(db):
    s = run(db, ["S011"], unreachable)
    assert not s.offline and s.statuses == {"error": 1}
    assert db.execute("SELECT consecutive_failures FROM sources WHERE source_id = 'S011'").fetchone()[0] == 1


def test_one_site_answering_means_the_internet_is_up(db):
    handler, _ = network()

    def google_down(request):
        if request.url.host == "news.google.com":
            raise httpx.ConnectError("connection refused", request=request)
        return handler(request)

    s = run(db, ["S011", gn_source_id(db)], google_down)
    assert not s.offline and s.statuses == {"ok": 1, "error": 1}


def test_google_refusals_are_counted(db):
    handler, _ = network()

    def refusing(request):
        return httpx.Response(429) if request.url.host == "news.google.com" else handler(request)

    s = run(db, ["S011", gn_source_id(db)], refusing)
    assert s.google_refusals == 1 and not s.offline
```

- [ ] **Step 2: Run them to see them fail**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_pipeline_cli.py -k "internet or unreachable or refusals"`
Expected: FAIL with `AttributeError: 'RunSummary' object has no attribute 'offline'`.

- [ ] **Step 3: Implement in `riffi_ingest/pipeline.py`**

Module docstring, add after the existing paragraph:

```python
"""...

A run where no site could be reached at all (this machine's internet was down) stores nothing per source, so no
source takes a strike for it and every one of them is still due at the next tick (D-008).
"""
```

Imports: replace `from .fetchers.http import PoliteClient` with the two lines below.

```python
from .fetchers.http import NETWORK_KINDS, PoliteClient, domain_key
from .fetchers.outcome import FetchOutcome
```

After the imports:

```python
# A host that cannot be reached at all; domain_down = the client gave up on a site after 3 such failures.
UNREACHABLE_KINDS = NETWORK_KINDS | {"domain_down"}
GOOGLE_NEWS = "news.google.com"
```

`RunSummary`: add two fields after `pruned_payloads`:

```python
    offline: bool = False  # no site could be reached: nothing was stored or counted against any source
    google_refusals: int = 0  # Google News feeds that answered 403 / 429 (D-004: the first sign of a block)
```

Two functions before `run_fetch`:

```python
def looks_offline(outcomes: list[FetchOutcome]) -> bool:
    """True when the problem was this machine's internet, not the sources: every source tried failed to reach
    its site, across two or more sites. One unreachable site on its own is that site's problem."""
    tried = [o for o in outcomes if o.status != "skipped"]
    if not tried or any(o.status != "error" or o.error_kind not in UNREACHABLE_KINDS for o in tried):
        return False
    return len({domain_key(o.url) for o in tried}) >= 2


def google_refusals(outcomes: list[FetchOutcome]) -> int:
    """Google News feeds that answered 403 or 429."""
    return sum(1 for o in outcomes if domain_key(o.url) == GOOGLE_NEWS and o.http_status in (403, 429))
```

In `run_fetch`, the `try` block becomes:

```python
    try:
        outcomes = await fetch_sources(
            client,
            sources,
            rsshub_base=rsshub_base(),
            validators=store.load_validators(conn),
            snapshots=store.load_snapshots(conn),
            on_done=on_progress,
        )
        summary.google_refusals = google_refusals(outcomes)
        if looks_offline(outcomes):
            summary.offline = True
            summary.statuses.update(o.status for o in outcomes)
            return summary
        resolver = Resolver(client, cache=cache)
        cleaned = await clean(outcomes, by_id, blocklist=blocklist, resolver=resolver, now=now)
    finally:
        if own_client:
            await client.aclose()
```

- [ ] **Step 4: Run the whole pipeline/CLI file**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_pipeline_cli.py`
Expected: all pass (the 4 new ones and every existing one).

- [ ] **Step 5: Commit**

```bash
git add riffi_ingest/pipeline.py tests/test_pipeline_cli.py
git commit -m "A run with no internet blames no source; count Google refusals"
```

---

### Task 3 (DANGEROUS - `riffi_ingest/db/**`): the run diary table and its queries

User job: the evidence for the day-14 report. For every 30 minutes of the test, the diary says whether the engine ran, found nothing due, was busy, was offline, failed or never finished. That is how a source's miss is told apart from the engine being down.

**Gate (heavy path):**
1. The **test-author** subagent writes Step 1's tests from the acceptance criteria, independently of the implementation.
2. An **adversarial-safety-reviewer quorum** (correctness; data safety and reversibility; does it hold up against the real 5 Oct `engine.db`) reviews Steps 3-4 below before they are applied.
3. The founder gives a plain-language yes. Then: `node .zuko/approve.js --files "riffi_ingest/db/schema.py,riffi_ingest/db/store.py" --reason "Add the run diary table (engine_runs) and its read/write functions" --ack "<founder's words>"`.
4. Apply, run the checks, then `node .zuko/approve.js --clear`.

**Files:**
- Modify: `riffi_ingest/db/schema.py` (docstring; append the table to `SCHEMA`)
- Modify: `riffi_ingest/db/store.py` (append six functions)
- Test: `tests/test_db.py` (append; extend the `store` import)

**Interfaces:**
- Consumes: `db.connection.utc_iso`, `from_iso`; existing `fetch_runs` and `sources` tables.
- Produces:
  - `store.last_attempted(conn) -> dict[str, datetime]`
  - `store.start_engine_run(conn, started_at: datetime, mode: str, sources_due: int) -> int`
  - `store.finish_engine_run(conn, engine_run_id: int, finished_at: datetime, outcome: str, *, sources_ok=None, sources_failed=None, sources_skipped=None, items_new=None, google_refusals=None, error=None) -> None`
  - `store.log_engine_tick(conn, at: datetime, mode: str, outcome: str, sources_due: int = 0) -> int`
  - `store.engine_runs_since(conn, since: datetime) -> list[sqlite3.Row]` (oldest first)
  - `store.last_engine_run(conn) -> sqlite3.Row | None` (skips `nothing_due` / `busy`)
  - `store.failing_sources(conn, at_least: int = 3) -> list[sqlite3.Row]` (`source_id`, `name`, `consecutive_failures`, `last_status`)
  - Table `engine_runs` columns: `engine_run_id`, `started_at`, `finished_at`, `mode`, `outcome`, `sources_due`, `sources_ok`, `sources_failed`, `sources_skipped`, `items_new`, `google_refusals`, `error`.

- [ ] **Step 1: Write the failing tests** - append to `tests/test_db.py`, and add to its `from riffi_ingest.db.store import (...)` list: `engine_runs_since`, `failing_sources`, `finish_engine_run`, `last_attempted`, `last_engine_run`, `log_engine_tick`, `start_engine_run`.

```python
# ---- the run diary (step 7)


def test_last_attempted_counts_every_status(db):
    record_fetch(db, FetchOutcome("S011", "Native publisher RSS/Atom", "ok"), NOW - timedelta(hours=3))
    record_fetch(db, FetchOutcome("S011", "Native publisher RSS/Atom", "error", reason="HTTP 500"), NOW - timedelta(hours=1))
    record_fetch(db, FetchOutcome("S112", "Manual", "skipped", reason="manual source"), NOW)
    assert last_attempted(db) == {"S011": NOW - timedelta(hours=1), "S112": NOW}


def test_the_run_diary_records_a_run_from_start_to_finish(db):
    rid = start_engine_run(db, NOW, "due", 12)
    row = db.execute("SELECT * FROM engine_runs WHERE engine_run_id = ?", (rid,)).fetchone()
    assert (row["outcome"], row["finished_at"], row["mode"], row["sources_due"]) == ("running", None, "due", 12)
    assert from_iso(row["started_at"]) == NOW
    finish_engine_run(
        db, rid, NOW + timedelta(minutes=11), "ok",
        sources_ok=10, sources_failed=1, sources_skipped=1, items_new=240, google_refusals=0,
    )
    row = db.execute("SELECT * FROM engine_runs WHERE engine_run_id = ?", (rid,)).fetchone()
    assert row["outcome"] == "ok" and from_iso(row["finished_at"]) == NOW + timedelta(minutes=11)
    counts = (row["sources_ok"], row["sources_failed"], row["sources_skipped"], row["items_new"], row["google_refusals"])
    assert counts == (10, 1, 1, 240, 0) and row["error"] is None


def test_timer_checks_that_fetch_nothing_are_kept_but_are_not_the_last_run(db):
    rid = start_engine_run(db, NOW, "due", 3)
    finish_engine_run(db, rid, NOW + timedelta(minutes=2), "ok", sources_ok=3, sources_failed=0, sources_skipped=0)
    log_engine_tick(db, NOW + timedelta(minutes=30), "due", "nothing_due")
    log_engine_tick(db, NOW + timedelta(minutes=60), "due", "busy", 4)
    assert [r["outcome"] for r in engine_runs_since(db, NOW - timedelta(hours=1))] == ["ok", "nothing_due", "busy"]
    assert [r["outcome"] for r in engine_runs_since(db, NOW + timedelta(minutes=30))] == ["nothing_due", "busy"]
    assert last_engine_run(db)["engine_run_id"] == rid
    busy = engine_runs_since(db, NOW + timedelta(minutes=60))[0]
    assert busy["finished_at"] == busy["started_at"] and busy["sources_due"] == 4


def test_a_diary_without_runs_has_no_last_run(db):
    assert last_engine_run(db) is None
    log_engine_tick(db, NOW, "due", "nothing_due")
    assert last_engine_run(db) is None


def test_failing_sources_are_those_with_three_strikes_or_more(db):
    for n in range(3):
        outcome = FetchOutcome("S034", "Native publisher RSS/Atom", "error", reason="HTTP 404")
        record_fetch(db, outcome, NOW + timedelta(hours=n))
    record_fetch(db, FetchOutcome("S011", "Native publisher RSS/Atom", "error", reason="HTTP 500"), NOW)
    assert [(r["source_id"], r["consecutive_failures"]) for r in failing_sources(db)] == [("S034", 3)]
    assert {r["source_id"] for r in failing_sources(db, at_least=1)} == {"S034", "S011"}


def test_an_existing_database_gains_the_diary_and_keeps_its_data(tmp_path, repo_root):
    path = tmp_path / "engine.db"
    conn = connect(path)
    import_sources(conn, repo_root / "feeds.csv", now=NOW)
    record_fetch(conn, FetchOutcome("S011", "Native publisher RSS/Atom", "ok"), NOW)
    conn.execute("DROP TABLE engine_runs")  # a file made before the diary existed (5 Oct 2026)
    conn.commit()
    conn.close()
    conn = connect(path)
    assert conn.execute("SELECT COUNT(*) FROM engine_runs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 131
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    conn.close()
```

- [ ] **Step 2: Run them to see them fail**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_db.py`
Expected: FAIL with `ImportError: cannot import name 'engine_runs_since'`.

- [ ] **Step 3 (after the founder's yes): `riffi_ingest/db/schema.py`**

Docstring becomes:

```python
"""The database schema. BRIEF.md "Data model" and step 6 name seven tables; four helpers are added:
topics (from topics.csv), gnews_cache (Google News link -> publisher URL, kept so a link is resolved
once ever, not once per run), schema_version, and engine_runs (the run diary, step 7).

Changing a table that already holds data needs a migration step here, never a drop: the two-week
test's history cannot be recreated. A new table is added with CREATE TABLE IF NOT EXISTS, which an existing
file picks up on its next connect without touching its data, so SCHEMA_VERSION stays as it is.
"""
```

Append inside the `SCHEMA` string, after the `gnews_cache` table:

```sql

-- The run diary (step 7, D-008): one row per fetch run and per timer check that fetched nothing, so every gap
-- in the fetch history has a known cause (laptop off or asleep, no internet, a crash). Kept forever.
CREATE TABLE IF NOT EXISTS engine_runs (
    engine_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,          -- the run's clock: equal to its fetch_runs.started_at
    finished_at TEXT,                  -- NULL while running, or if it never finished (killed, crashed)
    mode TEXT NOT NULL,                -- due (the timer) | all | source
    outcome TEXT NOT NULL,             -- running | ok | offline | failed | nothing_due | busy
    sources_due INTEGER NOT NULL DEFAULT 0,
    sources_ok INTEGER,
    sources_failed INTEGER,
    sources_skipped INTEGER,
    items_new INTEGER,
    google_refusals INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS engine_runs_started ON engine_runs (started_at);
```

- [ ] **Step 4 (after the founder's yes): `riffi_ingest/db/store.py`** - append at the end

```python
# ---- the run diary (step 7, D-008)


def last_attempted(conn: sqlite3.Connection) -> dict[str, datetime]:
    """When each source was last tried, whatever the result: the scheduler counts intervals from here."""
    rows = conn.execute("SELECT source_id, MAX(started_at) AS last FROM fetch_runs GROUP BY source_id")
    return {r["source_id"]: from_iso(r["last"]) for r in rows}


def start_engine_run(conn: sqlite3.Connection, started_at: datetime, mode: str, sources_due: int) -> int:
    """A diary row marked 'running'; finish_engine_run closes it. A row left 'running' is a run that never
    finished (killed or crashed)."""
    with conn:
        cur = conn.execute(
            "INSERT INTO engine_runs (started_at, mode, outcome, sources_due) VALUES (?, ?, 'running', ?)",
            (utc_iso(started_at), mode, sources_due),
        )
    return cur.lastrowid


def finish_engine_run(
    conn: sqlite3.Connection,
    engine_run_id: int,
    finished_at: datetime,
    outcome: str,
    *,
    sources_ok: int | None = None,
    sources_failed: int | None = None,
    sources_skipped: int | None = None,
    items_new: int | None = None,
    google_refusals: int | None = None,
    error: str | None = None,
) -> None:
    with conn:
        conn.execute(
            "UPDATE engine_runs SET finished_at = ?, outcome = ?, sources_ok = ?, sources_failed = ?,"
            " sources_skipped = ?, items_new = ?, google_refusals = ?, error = ? WHERE engine_run_id = ?",
            (
                utc_iso(finished_at),
                outcome,
                sources_ok,
                sources_failed,
                sources_skipped,
                items_new,
                google_refusals,
                error,
                engine_run_id,
            ),
        )


def log_engine_tick(conn: sqlite3.Connection, at: datetime, mode: str, outcome: str, sources_due: int = 0) -> int:
    """A timer check that fetched nothing ('nothing_due', or 'busy' while another run held the lock): one
    finished row, so the diary shows the engine was awake at that time."""
    stamp = utc_iso(at)
    with conn:
        cur = conn.execute(
            "INSERT INTO engine_runs (started_at, finished_at, mode, outcome, sources_due) VALUES (?, ?, ?, ?, ?)",
            (stamp, stamp, mode, outcome, sources_due),
        )
    return cur.lastrowid


def engine_runs_since(conn: sqlite3.Connection, since: datetime) -> list[sqlite3.Row]:
    """Diary rows started at or after `since`, oldest first."""
    return conn.execute(
        "SELECT * FROM engine_runs WHERE started_at >= ? ORDER BY started_at, engine_run_id", (utc_iso(since),)
    ).fetchall()


def last_engine_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The latest diary row that fetched, or tried to: checks with nothing due or a busy lock are skipped."""
    return conn.execute(
        "SELECT * FROM engine_runs WHERE outcome NOT IN ('nothing_due', 'busy')"
        " ORDER BY started_at DESC, engine_run_id DESC LIMIT 1"
    ).fetchone()


def failing_sources(conn: sqlite3.Connection, at_least: int = 3) -> list[sqlite3.Row]:
    """Active sources that failed `at_least` runs in a row (BRIEF.md: flagged for replacement after 3)."""
    return conn.execute(
        "SELECT source_id, name, consecutive_failures, last_status FROM sources"
        " WHERE active = 1 AND consecutive_failures >= ? ORDER BY consecutive_failures DESC, source_id",
        (at_least,),
    ).fetchall()
```

- [ ] **Step 5: Run the database tests, then the whole suite**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_db.py` then `node .claude/hooks/py.js -m pytest -q tests`
Expected: all pass.

- [ ] **Step 6: Check against a copy of the real database (laptop)**

Copy `data/engine.db` to the session scratchpad, `connect()` the copy, and check that the counts of `sources`, `fetch_runs`, `items`, `story_clusters`, `item_topics`, `page_snapshots` and `gnews_cache` match the original (131 / 131 / 2,177 / 2,065 / 1,368 / 14 / 100 on 5 Oct), that `engine_runs` exists and is empty, and that `schema_version` is 1. The real file is not touched.

- [ ] **Step 7: Commit, then clear the approval**

```bash
git add riffi_ingest/db/schema.py riffi_ingest/db/store.py tests/test_db.py
git commit -m "Database: the run diary (engine_runs) and its queries (founder-approved)"
node .zuko/approve.js --clear
```

---

### Task 4: `fetch --due`, and every fetch written to the diary and the log

User job: the command Windows' timer runs. The founder never types it. Every run, and every check that fetched nothing, leaves a trace.

**Files:**
- Modify: `riffi_ingest/cli.py` (docstring, imports, options, `fetch`, two helpers)
- Test: `tests/test_pipeline_cli.py` (append; update one assertion in `test_cli_rejects_unclear_requests`)

**Interfaces:**
- Consumes: Task 1 (`Schedule`, `due`), Task 2 (`RunSummary.offline`, `.google_refusals`), Task 3 (`store.last_attempted`, `start_engine_run`, `finish_engine_run`, `log_engine_tick`), `runlock.run_lock` / `AlreadyRunning`, `pipeline.run_fetch(conn, sources, *, paths, client, now, on_progress)`.
- Produces: CLI `fetch --due [--schedule PATH]`; `ScheduleOption` (reused by `status` in Task 5); `engine.log` beside the database; diary rows with mode `due` / `all` / `source`.

- [ ] **Step 1: Write the failing tests** - append to `tests/test_pipeline_cli.py`, and add `from riffi_ingest.runlock import run_lock` to its imports.

```python
# ---- fetch --due: what Windows' timer runs

ROUTES = (
    "Google News RSS",
    "X/Instagram via RSS.app",
    "Native publisher RSS/Atom",
    "YouTube Atom",
    "RSSHub Telegram",
    "Web page monitor",
    "Manual",
)


def schedule_file(tmp_path, speeds):
    """Every route type 'never', plus per-source `speeds`, so a test fetches exactly the sources it names."""
    lines = ["early_minutes: 5", "by_route:", *(f"  {r}: never" for r in ROUTES), "sources:"]
    lines += [f"  {source_id}: {speed}" for source_id, speed in speeds.items()]
    path = tmp_path / "schedule.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def prepared_db(tmp_path, repo_root):
    db_path = tmp_path / "engine.db"
    conn = connect(db_path)
    import_sources(conn, repo_root / "feeds.csv")
    import_topics(conn, repo_root / "topics.csv")
    conn.execute("UPDATE sources SET fetch_url = ? WHERE source_id = 'S011'", (DH_FEED,))
    conn.commit()
    return db_path, conn


def fetch_due(db_path, schedule):
    return CliRunner().invoke(cli.app, ["fetch", "--due", "--db", str(db_path), "--schedule", str(schedule)])


def test_fetch_due_fetches_what_is_due_and_writes_the_diary(tmp_path, repo_root, monkeypatch):
    handler, calls = network()
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(handler))
    db_path, conn = prepared_db(tmp_path, repo_root)
    schedule = schedule_file(tmp_path, {"S011": "2h"})
    r = fetch_due(db_path, schedule)
    assert r.exit_code == 0, r.output
    assert calls == ["www.deccanherald.com"]  # only the one source that is due
    row = conn.execute("SELECT * FROM engine_runs").fetchone()
    assert (row["mode"], row["outcome"], row["sources_due"], row["sources_ok"], row["items_new"]) == (
        "due",
        "ok",
        1,
        1,
        1,
    )
    assert row["finished_at"] is not None
    assert conn.execute("SELECT started_at FROM fetch_runs").fetchone()[0] == row["started_at"]
    log = (tmp_path / "engine.log").read_text(encoding="utf-8")
    assert " due " in log and " ok " in log and "new items 1" in log
    again = fetch_due(db_path, schedule)  # straight away: nothing is due for another 2 hours
    assert again.exit_code == 0 and "Nothing is due" in again.output
    outcomes = [x[0] for x in conn.execute("SELECT outcome FROM engine_runs ORDER BY engine_run_id")]
    assert outcomes == ["ok", "nothing_due"]
    assert "nothing_due" in (tmp_path / "engine.log").read_text(encoding="utf-8")


def test_a_check_while_a_run_is_going_is_skipped_and_noted(tmp_path, repo_root):
    db_path, conn = prepared_db(tmp_path, repo_root)
    schedule = schedule_file(tmp_path, {"S011": "2h"})
    with run_lock(tmp_path / "fetch.lock"):
        r = fetch_due(db_path, schedule)
        by_hand = CliRunner().invoke(cli.app, ["fetch", "--all", "--db", str(db_path)])
    assert r.exit_code == 0 and "still running" in r.output
    assert by_hand.exit_code == 1 and "already running" in by_hand.output
    assert [tuple(x) for x in conn.execute("SELECT outcome, sources_due FROM engine_runs")] == [("busy", 1)]


def test_a_crashed_run_is_marked_failed_with_its_reason(tmp_path, repo_root, monkeypatch):
    async def disk_full(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cli, "run_fetch", disk_full)
    db_path, conn = prepared_db(tmp_path, repo_root)
    r = fetch_due(db_path, schedule_file(tmp_path, {"S011": "2h"}))
    assert r.exit_code == 1 and "disk full" in r.output
    row = conn.execute("SELECT outcome, error, finished_at FROM engine_runs").fetchone()
    assert row["outcome"] == "failed" and "RuntimeError: disk full" in row["error"] and row["finished_at"]
    assert "RuntimeError: disk full" in (tmp_path / "engine.log").read_text(encoding="utf-8")


def test_no_internet_is_recorded_as_offline(tmp_path, repo_root, monkeypatch):
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(unreachable))
    db_path, conn = prepared_db(tmp_path, repo_root)
    r = fetch_due(db_path, schedule_file(tmp_path, {"S011": "2h", gn_source_id(conn): "2h"}))
    assert r.exit_code == 0 and "No internet" in r.output
    assert tuple(conn.execute("SELECT outcome, sources_failed FROM engine_runs").fetchone()) == ("offline", 2)
    assert conn.execute("SELECT COUNT(*) FROM fetch_runs").fetchone()[0] == 0


def test_runs_by_hand_are_in_the_diary_too(tmp_path, repo_root, monkeypatch):
    handler, _ = network()
    monkeypatch.setattr(pipeline, "PoliteClient", lambda: fake_client(handler))
    db_path, conn = prepared_db(tmp_path, repo_root)
    r = CliRunner().invoke(cli.app, ["fetch", "--source", "S011", "--db", str(db_path)])
    assert r.exit_code == 0, r.output
    assert tuple(conn.execute("SELECT mode, outcome FROM engine_runs").fetchone()) == ("source", "ok")


def test_a_bad_schedule_file_stops_with_its_name(tmp_path, repo_root):
    db_path, _ = prepared_db(tmp_path, repo_root)
    bad = tmp_path / "schedule.yaml"
    bad.write_text("by_route:\n  Google News RSS: fortnightly\n", encoding="utf-8")
    r = fetch_due(db_path, bad)
    assert r.exit_code == 1 and "fortnightly" in r.output
```

In `test_cli_rejects_unclear_requests`, the message changes with the new option. Replace the line
`assert both.exit_code == 1 and "not both" in both.output`
with:

```python
    assert both.exit_code == 1 and "exactly one of" in both.output
    due_and_all = runner.invoke(cli.app, ["fetch", "--due", "--all", "--db", db_path, "--feeds", feeds])
    assert due_and_all.exit_code == 1 and "exactly one of" in due_and_all.output
```

- [ ] **Step 2: Run them to see them fail**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_pipeline_cli.py`
Expected: the 6 new tests and `test_cli_rejects_unclear_requests` FAIL (`No such option: --due` / message mismatch).

- [ ] **Step 3: Implement in `riffi_ingest/cli.py`**

Docstring command list becomes:

```python
"""Command line (BRIEF.md step 7): python -m riffi_ingest <command>.

    import-sources    seed / refresh sources from feeds.csv (idempotent)
    import-topics     seed / refresh topics from topics.csv (idempotent)
    test-feeds        fetch every source once and report pass / fail with a suggested fix; stores nothing
    fetch             one full cycle (fetch, clean, group, tag, score, store): --all, --source S004 --source S011,
                      or --due (only the sources whose interval is up: what Windows' timer runs every 30 minutes)
    stories           the best stories of the last 24 hours with score, label, sources and topics
    status            is the engine alive: the last run, the last 24 hours, sources failing 3+ runs in a row

Coming with later steps: digest --date and report.
"""
```

Imports: add `import textwrap` and `import traceback` (standard library block); add `from .scheduler import Schedule, ScheduleError, due` (after `from .runlock ...`).

Options, after `LabelOption`:

```python
DueOption = typer.Option(
    False, "--due", help="Only the sources whose interval is up (config/schedule.yaml): what Windows' timer runs."
)
ScheduleOption = typer.Option(
    PROJECT_ROOT / "config" / "schedule.yaml", "--schedule", help="How often each source is fetched."
)
```

Two helpers before `fetch` (the first is the existing first-run code moved out of `fetch` unchanged):

```python
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
    """One line per run in engine.log beside the database. Windows' timer runs the engine with no window, so
    this file and `status` are how anyone sees what happened."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = f"{when.astimezone(IST):%Y-%m-%d %H:%M} IST  {mode:<6} {outcome:<11} {detail}".rstrip()
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
```

`fetch` becomes:

```python
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
    conn = connect(db_path)
    log = db_path.parent / "engine.log"
    _first_run_imports(conn, feeds, topics)
    now = datetime.now(timezone.utc)
    if source:  # by id, inactive sources included, so a person can still try one by hand
        mode = "source"
        ids = [w.strip().upper() for w in source]
        sources = store.load_sources(conn, ids)
        unknown = sorted(set(ids) - {s.source_id for s in sources})
        if unknown:
            _fail(f"no such source id: {', '.join(unknown)} (see source_id in feeds.csv)")
    elif all_sources:
        mode, sources = "all", store.load_sources(conn)
    else:
        mode, active = "due", store.load_sources(conn)
        try:
            speeds = Schedule.load(schedule)
        except (OSError, ScheduleError) as exc:
            _log(log, now, mode, "failed", str(exc))
            _fail(str(exc))
        for problem in speeds.problems(active):
            typer.echo(f"Warning: {problem} in {schedule}; those sources are never fetched.")
            _log(log, now, mode, "warning", problem)
        sources = due(active, store.last_attempted(conn), speeds, now)
        if not sources:
            store.log_engine_tick(conn, now, mode, "nothing_due")
            _log(log, now, mode, "nothing_due")
            typer.echo("Nothing is due yet.")
            return
    typer.echo(
        f"Fetching {len(sources)} sources; expect up to about {_estimate_minutes(sources, with_lookups=True)} minutes"
        " (sites are asked politely, one request every 2 s each). Please leave it running. Progress:"
    )
    try:
        with run_lock(db_path.parent / "fetch.lock"):
            run_id = store.start_engine_run(conn, now, mode, len(sources))
            try:
                s = asyncio.run(run_fetch(conn, sources, paths=Paths(), now=now, on_progress=_progress(len(sources))))
            except Exception as exc:
                store.finish_engine_run(
                    conn, run_id, datetime.now(timezone.utc), "failed", error=f"{type(exc).__name__}: {exc}"[:500]
                )
                trace = textwrap.indent(traceback.format_exc().rstrip(), "    ")
                _log(log, now, mode, "failed", f"due {len(sources)}\n{trace}")
                _fail(f"the run failed: {exc} (details in {log})")
            ok = s.statuses["ok"] + s.statuses["not_modified"]
            failed, skipped = s.statuses["error"], s.statuses["skipped"]
            finished = datetime.now(timezone.utc)
            outcome = "offline" if s.offline else "ok"
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
            _log(
                log,
                now,
                mode,
                outcome,
                f"due {len(sources)}, ok {ok}, failed {failed}, skipped {skipped}, new items {s.items_new},"
                f" google refusals {s.google_refusals}, {(finished - now).total_seconds() / 60:.1f} min",
            )
    except AlreadyRunning as exc:
        if mode != "due":
            _fail(str(exc))
        store.log_engine_tick(conn, now, mode, "busy", len(sources))
        _log(log, now, mode, "busy", f"due {len(sources)}")
        typer.echo("Another fetch is still running; this check is skipped.")
        return
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
    typer.echo(f"Entries read: {s.entries}; new items stored: {s.items_new}.")
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
```

Note: `_fail` raises `typer.Exit`, so it never returns; the code after a `_fail` call in an `except` block does not run.

- [ ] **Step 4: Run the tests**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_pipeline_cli.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add riffi_ingest/cli.py tests/test_pipeline_cli.py
git commit -m "fetch --due for Windows' timer; every fetch goes into the run diary and engine.log"
```

---

### Task 5: `status` - is it alive?

User job: the founder (or Zuko) sees in a few seconds that the engine is running, how its last run went, whether any time went missing, whether Google is pushing back, and which sources need fixing.

**Files:**
- Create: `riffi_ingest/runstatus.py`
- Modify: `riffi_ingest/cli.py` (import `runstatus`; `status` command after `stories`)
- Test: `tests/test_runstatus.py`

**Interfaces:**
- Consumes: Task 3 (`store.last_engine_run`, `engine_runs_since`, `failing_sources`, `start_engine_run`, `finish_engine_run`, `log_engine_tick`), Task 1 (`Schedule.load(...).problems`), Task 4 (`ScheduleOption`).
- Produces: `runstatus.report(conn, now) -> list[str]`; CLI `status [--db PATH] [--schedule PATH]`.

- [ ] **Step 1: Write the failing tests** - `tests/test_runstatus.py`

```python
from datetime import datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from riffi_ingest import cli, runstatus
from riffi_ingest.db import connect
from riffi_ingest.db.importers import import_sources
from riffi_ingest.db.store import finish_engine_run, log_engine_tick, record_fetch, start_engine_run
from riffi_ingest.fetchers.outcome import FetchOutcome

NOW = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)  # 11:30 IST


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
        at += timedelta(minutes=30)


def text(conn):
    return "\n".join(runstatus.report(conn, NOW))


def test_before_any_run(db):
    assert "has not run yet" in text(db)


def test_a_healthy_day(db):
    ticks(db, NOW - timedelta(hours=23, minutes=30), NOW - timedelta(minutes=30))
    a_run(db, NOW - timedelta(minutes=10))
    out = text(db)
    assert "Last run: 2026-10-10 11:20 IST (10 min ago), the sources that were due: done in 8 min." in out
    assert "120 sources: 110 worked, 6 failed, 4 skipped; 240 new articles." in out
    assert "Last 24 hours: 48 checks - 47 nothing due, 1 ran." in out
    assert "No gaps" in out and "Google refusals: 0." in out
    assert "Sources failing 3+ runs in a row: none." in out


def test_a_gap_is_shown_with_its_times(db):
    ticks(db, NOW - timedelta(hours=20), NOW - timedelta(hours=12))
    ticks(db, NOW - timedelta(hours=6), NOW)
    a_run(db, NOW - timedelta(hours=6))
    assert "Longest gap with no check: 6.0 h, 2026-10-09 23:30 IST to 2026-10-10 05:30 IST" in text(db)


def test_a_run_that_never_finished(db):
    a_run(db, NOW - timedelta(hours=3), outcome="running")
    ticks(db, NOW - timedelta(hours=2, minutes=30), NOW)
    out = text(db)
    assert "did not finish (stopped or crashed)" in out and "1 did not finish" in out


def test_a_run_still_going(db):
    a_run(db, NOW - timedelta(minutes=5), outcome="running")
    assert ": running now." in text(db)


def test_failed_and_offline_runs_are_explained(db):
    a_run(db, NOW - timedelta(minutes=40), outcome="failed", minutes=1, error="RuntimeError: disk full")
    out = text(db)
    assert "FAILED after 1 min - RuntimeError: disk full" in out
    a_run(db, NOW - timedelta(minutes=10), outcome="offline", minutes=1, ok=0, failed=116)
    out = text(db)
    assert "no internet - none of the 120 sources could be reached" in out
    assert "1 failed" in out and "1 offline (no internet)" in out


def test_google_refusals_raise_a_warning(db):
    a_run(db, NOW - timedelta(minutes=10), refusals=3)
    assert "Google refusals: 3 - WARNING" in text(db)


def test_sources_failing_three_runs_in_a_row_are_listed(db):
    for n in range(3):
        outcome = FetchOutcome("S057", "Native publisher RSS/Atom", "error", reason="HTTP 404")
        record_fetch(db, outcome, NOW - timedelta(hours=n))
    a_run(db, NOW - timedelta(minutes=10))
    out = text(db)
    assert "Sources failing 3+ runs in a row (1;" in out
    assert "  S057 Vijaya Karnataka - 3 in a row - error: HTTP 404" in out


def test_a_day_with_no_checks_says_so(db):
    a_run(db, NOW - timedelta(days=2))
    out = text(db)
    assert "(2.0 days ago)" in out and "no checks at all" in out


def test_the_status_command(tmp_path):
    r = CliRunner().invoke(cli.app, ["status", "--db", str(tmp_path / "e.db")])
    assert r.exit_code == 0 and "has not run yet" in r.output
```

- [ ] **Step 2: Run them to see them fail**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_runstatus.py`
Expected: FAIL with `ImportError: cannot import name 'runstatus'`.

- [ ] **Step 3: Write `riffi_ingest/runstatus.py`**

```python
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
RUN_LIMIT = timedelta(hours=1)  # Windows' timer stops a run after an hour (scripts/schedule-windows.ps1)
NORMAL_GAP = timedelta(minutes=45)  # the timer checks every 30 minutes; a longer silence is worth showing

MODES = {"due": "the sources that were due", "all": "all sources", "source": "chosen sources"}
OUTCOMES = {
    "ok": "ran",
    "offline": "offline (no internet)",
    "failed": "failed",
    "nothing_due": "nothing due",
    "busy": "skipped (a run was still going)",
    "running": "running now",
}


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


def _unfinished(row: sqlite3.Row, now: datetime) -> bool:
    return row["outcome"] == "running" and now - from_iso(row["started_at"]) > RUN_LIMIT


def report(conn: sqlite3.Connection, now: datetime) -> list[str]:
    last = store.last_engine_run(conn)
    if last is None:
        return ["The engine has not run yet. Run it once with: python -m riffi_ingest fetch --all"]
    return [
        _last_run(last, now),
        *_last_day(store.engine_runs_since(conn, now - DAY), now),
        *_failing(store.failing_sources(conn)),
    ]


def _last_run(row: sqlite3.Row, now: datetime) -> str:
    started = from_iso(row["started_at"])
    head = f"Last run: {_when(started)} ({_span(now - started)} ago), {MODES.get(row['mode'], row['mode'])}"
    if row["outcome"] == "running":
        return head + (": did not finish (stopped or crashed)." if _unfinished(row, now) else ": running now.")
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


def _last_day(rows: list[sqlite3.Row], now: datetime) -> list[str]:
    if not rows:
        return ["Last 24 hours: no checks at all - the timer is not running, or the laptop was off or asleep."]
    counts = Counter("did not finish" if _unfinished(r, now) else OUTCOMES.get(r["outcome"], r["outcome"]) for r in rows)
    lines = [f"Last 24 hours: {len(rows)} checks - " + ", ".join(f"{n} {what}" for what, n in counts.most_common()) + "."]
    times = [from_iso(r["started_at"]) for r in rows] + [now]
    gap, start, end = max((b - a, a, b) for a, b in zip(times, times[1:]))
    if gap > NORMAL_GAP:
        lines.append(
            f"Longest gap with no check: {_span(gap)}, {_when(start)} to {_when(end)}"
            " (the laptop was off or asleep, or the timer was not running)."
        )
    else:
        lines.append("No gaps: the engine checked at least every 45 minutes.")
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
```

- [ ] **Step 4: Add the `status` command to `riffi_ingest/cli.py`**

Import: change `from . import health` to `from . import health, runstatus`. After the `stories` command:

```python
@app.command("status")
def status(db: Path = DbOption, schedule: Path = ScheduleOption) -> None:
    """Is the engine alive? The last run, the last 24 hours (checks, gaps, Google refusals) and the sources
    failing 3+ runs in a row. Changes nothing."""
    conn = connect(db)
    for line in runstatus.report(conn, datetime.now(timezone.utc)):
        typer.echo(line)
    for problem in Schedule.load(schedule).problems(store.load_sources(conn)):
        typer.echo(f"Warning: {problem} in {schedule}; those sources are never fetched.")
```

- [ ] **Step 5: Run the tests**

Run: `node .claude/hooks/py.js -m pytest -q tests/test_runstatus.py` then `node .claude/hooks/py.js -m pytest -q tests`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add riffi_ingest/runstatus.py riffi_ingest/cli.py tests/test_runstatus.py
git commit -m "status: is the engine alive (last run, last 24 hours, failing sources)"
```

---

### Task 6: The Windows timer script

User job: makes the engine start itself every 30 minutes on the founder's laptop, with nothing to keep open.

**Files:**
- Create: `scripts/schedule-windows.ps1`

**Interfaces:**
- Consumes: `.venv\Scripts\pythonw.exe`, CLI `fetch --due` (Task 4).
- Produces: a Windows scheduled task named `Riffi ingestion engine - fetch`.

- [ ] **Step 1: Write `scripts/schedule-windows.ps1`**

```powershell
<#
Registers (or removes) the Windows Task Scheduler entry that runs the engine every 30 minutes (D-008).

    powershell -ExecutionPolicy Bypass -File scripts\schedule-windows.ps1            # install, or replace
    powershell -ExecutionPolicy Bypass -File scripts\schedule-windows.ps1 -Remove    # remove

The task runs `.venv\Scripts\pythonw.exe -m riffi_ingest fetch --due` from the project folder with no window,
only while this user is logged on (no password is stored), also on battery. After a missed start (laptop off
or asleep) it runs as soon as it can. Never two at once; Windows stops a run after 1 hour.
Check on it with: .venv\Scripts\python.exe -m riffi_ingest status
#>
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$TaskName = 'Riffi ingestion engine - fetch'
$Project = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "Removed '$TaskName' (if it was there)."
    return
}

$Pythonw = Join-Path $Project '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $Pythonw)) { throw "Cannot find $Pythonw - set up the virtual environment first (README)." }

$now = Get-Date
$start = $now.Date.AddHours($now.Hour).AddMinutes(30 * ([math]::Floor($now.Minute / 30) + 1))  # next :00 or :30
$action = New-ScheduledTaskAction -Execute $Pythonw -Argument '-m riffi_ingest fetch --due' -WorkingDirectory $Project
$trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes 30)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description 'Riffi ingestion engine: fetch the sources that are due (config/schedule.yaml). See README.' | Out-Null
Write-Output "Installed '$TaskName': every 30 minutes from $($start.ToString('yyyy-MM-dd HH:mm'))."
```

- [ ] **Step 2: Check the script parses (no registration)**

Run (PowerShell): `$null = [System.Management.Automation.Language.Parser]::ParseFile("$PWD\scripts\schedule-windows.ps1", [ref]$null, [ref]$errs); $errs.Count`
Expected: `0`.

- [ ] **Step 3: Check that `pythonw` runs the CLI with no console**

Run (PowerShell): `.venv\Scripts\pythonw.exe -m riffi_ingest fetch --due --db "<scratchpad>\copy.db" --schedule "<scratchpad>\never.yaml"`, where `never.yaml` sets every route to `never`. Then read `<scratchpad>\engine.log`.
Expected: a `nothing_due` line in the log, and no crash from the missing console (click's `echo` skips a missing stdout).

- [ ] **Step 4: Commit**

```bash
git add scripts/schedule-windows.ps1
git commit -m "Windows timer: register fetch --due every 30 minutes"
```

Registering the task on the laptop happens only after the PR is merged and `main` is checked out (the task runs whatever code is in the folder), and only with the founder's yes. See "After merge" below.

---

### Task 7: Documents (DECISIONS, rules file, specs, plan, README)

User job: anyone reading the docs knows the engine now runs on its own, at what speeds, why it departs from the brief, and how to check on it.

**Files:**
- Modify: `DECISIONS.md` (D-008 on top)
- Modify (DANGEROUS, after the founder's yes + `approve.js`): `CLAUDE.md` (Buy-not-built prose line and the `buyNotBuilt` entry in the config block), `.zuko/config.json` (the same `buyNotBuilt` entry)
- Modify: `docs/technical-spec.md`, `docs/implementation-plan.md`, `README.md`

- [ ] **Step 1: `DECISIONS.md`** - insert above D-007:

```markdown
## D-008 · The engine runs on Windows' own timer; Google News every 2 h during the two-week test (5 Oct 2026, founder)

The brief's step 7 names APScheduler and these speeds: every 30 min for Google News queries on High-priority topics, Telegram and the 5 priority X feeds; every 2 h for all other feeds; every 6 h for page monitors.

Instead:
- **Windows Task Scheduler** starts `python -m riffi_ingest fetch --due` every 30 minutes (`scripts/schedule-windows.ps1`; the server's own timer after the move, D-003). The engine fetches only the sources whose interval is up, counted from each source's last attempt, so a sleep, reboot or crash costs one catch-up run, never a pile-up. No long-running process and no new library.
- **Speeds** live in `config/schedule.yaml` (team-editable: per route type, with per-source overrides). During the two-week test: Telegram every 30 min; Google News (including the X/Instagram backups), publisher feeds and YouTube every 2 h; page monitors every 6 h.
- **A run diary** (`engine_runs`) records every run and every timer check that found nothing due or a run still going. A run where every source failed to reach its site, across two or more sites, is "offline" and counts against no source. `status` shows the last run, gaps, Google refusals and failing sources.

Why: `feeds.csv` does not say which topics a Google News query covers (`topics_hint` is free text), so "Google News feeds tied to High-priority topics" could not be listed. 2 h already meets the 24 h recall target. Fewer requests to Google lower the risk of a block that would end the test (D-004). Before launch, feeds that broke news first during the test can be moved to 30 min in `config/schedule.yaml`. A timer owned by Windows survives reboots and crashes with no window left open.
```

- [ ] **Step 2 (after the founder's yes): `CLAUDE.md` and `.zuko/config.json`**

`node .zuko/approve.js --files "CLAUDE.md,.zuko/config.json" --reason "Scheduling line: APScheduler -> Windows Task Scheduler (D-008)" --ack "<founder's words>"`

In `CLAUDE.md` Part B "Buy-not-built map", replace `**Scheduling** - APScheduler.` with `**Scheduling** - Windows Task Scheduler (\`scripts/schedule-windows.ps1\`; the server's own timer later), D-008.`

In the `CLAUDE.md` config block and in `.zuko/config.json`, the entry becomes:

```json
    {
      "surface": "scheduling / CLI / dashboard",
      "service": "Windows Task Scheduler (the server's timer later, D-008) / Typer / FastAPI on 127.0.0.1"
    },
```

Then check that the two JSON copies are still identical: extract the fenced json block from `CLAUDE.md`, `json.loads` both, compare equal. Then `node .zuko/approve.js --clear`.

- [ ] **Step 3: `docs/technical-spec.md`**
- Header status line: steps 1-7 built (scheduler as-built, 5 Oct 2026).
- Stack: `Scheduling: APScheduler.` becomes `Scheduling: Windows Task Scheduler runs \`fetch --due\` every 30 min (D-008); no scheduler library.`
- Planned layout: `scheduler.py` line becomes `(as built) speeds from config/schedule.yaml and the due rule`; add `runstatus.py  (as built) what \`status\` shows`; add `config/schedule.yaml  speeds per route type / source (team-tunable)`; add `scripts/schedule-windows.ps1  registers the Windows task`; `cli.py` line gains `fetch --due, status`.
- New section "Scheduler (as built, 5 Oct 2026)": the tick flow; the due rule (last attempt = latest `fetch_runs.started_at`; due at `interval - early_minutes`); the offline rule (every tried outcome unreachable, two or more sites; nothing stored; known limit for a Telegram-only tick); Google refusals; `engine.log` beside the database; `status`; the Windows task settings.
- Data model: add **engine_runs** (columns and outcomes; about 48 rows a day; kept forever).

- [ ] **Step 4: `docs/implementation-plan.md`**
- Status line: 5 Oct 2026: steps 1-6 and step 7 built (CLI + scheduler); first live run done; next: install the timer on the laptop, the founder's decisions A-C from the handoff, then outputs (8) and the AI pass.
- Step 7 row: `Built (5 Oct 2026): CLI and scheduler (\`fetch --due\` every 30 min through Windows Task Scheduler, D-008), run diary, \`status\`. Digest and report come with step 8`.
- "First run" row: `Done 5 Oct 2026 on the laptop: test-feeds 96 passed / 35 failed; fetch --all 115 ok, 11 errors, 5 skipped, 2,177 new items, 2,065 stories (3 High, 610 Medium, 348 Low, 1,104 Drop). Follow-ups in docs/zuko-handoff.md`.
- Open question 2 stays (it is about the AI pass).
- Drift log: `5 Oct 2026 - The brief's step 7 speeds (Google News on High-priority topics every 30 min) and APScheduler gave way to D-008: Windows Task Scheduler and \`fetch --due\`, Google News every 2 h during the test. The log is \`engine.log\` beside the database (like \`fetch.lock\`), not a separate logs folder, so test databases never write into the real log.`

- [ ] **Step 5: `README.md`**
- "Built so far" gains the scheduler and `status`; "Not yet" loses the scheduler.
- Commands table: `fetch` row mentions `--due`; new row for `status`.
- New section "Running on its own (the two-week test)": what the timer does; Zuko installs it (`scripts/schedule-windows.ps1`, `-Remove` to stop it); speeds in `config/schedule.yaml`; `status` to check on it; `data/engine.log`; keep the laptop plugged in and set not to sleep on mains power (a laptop setting only its owner changes; if it sleeps, the engine catches up and `status` shows the gap).
- "Files the team edits": add `config/schedule.yaml`.

- [ ] **Step 6: Run every check**

Run: `node .claude/hooks/py.js -m ruff check .`, `node .claude/hooks/py.js -m ruff format --check .`, `node .claude/hooks/py.js -m pytest -q tests`
Expected: all clean; tests 181 + 9 + 4 + 6 + 6 + 10 = 216 passed.

- [ ] **Step 7: Commit**

```bash
git add DECISIONS.md CLAUDE.md .zuko/config.json docs/technical-spec.md docs/implementation-plan.md README.md
git commit -m "Docs: D-008, the scheduler as built, and the runbook"
```

---

### After merge (laptop, founder's yes required)

1. `git checkout main && git pull` (the timer runs the code in this folder).
2. Run `powershell -ExecutionPolicy Bypass -File scripts\schedule-windows.ps1`.
3. Verify the task:
   - `Get-ScheduledTask -TaskName 'Riffi ingestion engine - fetch' | Select-Object State`;
   - `.Triggers[0].Repetition`: the interval is 30 minutes and the duration is empty (indefinitely);
   - `.Settings`: `DisallowStartIfOnBatteries` is False and `StartWhenAvailable` is True.
4. `Start-ScheduledTask -TaskName 'Riffi ingestion engine - fetch'`, wait for it, then `python -m riffi_ingest status` and read `data/engine.log`. Since the 5 Oct run is more than 2 h old, the first tick should be a catch-up run of every active source.
5. Over the next ticks: Telegram in every tick, other feeds every 4th tick, page monitors every 12th, in `fetch_runs`.
