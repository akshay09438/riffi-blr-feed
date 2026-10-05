# Scheduler design - the engine runs on its own (BRIEF step 7)

*Approved by the founder on 5 Oct 2026 in a /zuko:build session. Decisions taken: Google News every 2 h during the two-week test; Windows' own timer (Task Scheduler) instead of APScheduler.*

## Why

The two-week source test has to start by about 10-12 Oct 2026. The real job here is **14 days of trustworthy, unattended fetching, where every gap has a known cause**. A gap might mean the laptop was asleep, the internet was down, or the source itself failed. If those causes are told apart, the day-14 keep/fix/drop verdicts are fair to each source. Today the engine only runs when a person types `fetch --all`.

Who it serves now: the two-week test (and the editor whose log it is scored against), and the founder, who needs to see in seconds that it is alive. The content team and seed agent benefit later (step 8 reads what this collects).

**Did it work:** without anyone typing anything, a run happens every 30 minutes. Each source is fetched at its speed: Telegram 30 min, feeds 2 h, page monitors 6 h. A Wi-Fi-off period shows as "offline" and blames no source.
**Did it help:** in the first 48 h, these hold:
- no manual runs are needed;
- no two runs overlap;
- each run finishes in under 15 minutes;
- Google refusals stay at 0;
- every gap in the run diary is explained by the laptop being off or asleep.

**Non-goals:** the 07:00 digest, health report and dashboard (step 8/9); scheduling the AI pass; any change to `fetchers/http.py` or `fetchers/gnews.py`; disabling sources automatically; waking the laptop from sleep; running while logged out (that would store a Windows password).

## Speeds - `config/schedule.yaml` (new, team-editable)

```yaml
early_minutes: 5          # a source is due this many minutes before its interval is up (timer jitter)
by_route:                 # route_type (feeds.csv) -> how often
  Google News RSS: 2h
  X/Instagram via RSS.app: 2h      # phase 1 reads the backup Google News query
  Native publisher RSS/Atom: 2h
  YouTube Atom: 2h
  RSSHub Telegram: 30m
  Web page monitor: 6h
  Manual: never
sources: {}               # per-source overrides, e.g. S016: 30m
```

Durations are `<n>m`, `<n>h` or `never`. Loading fails with a clear message on a bad value. A check (and a test against the real `feeds.csv`) lists any route type without a speed and any override for an unknown source id.

This departs from the brief's speeds, which put High-priority Google News queries and the priority X feeds at 30 min. The departure is recorded as **D-008**. The brief's list of "Google News feeds tied to High-priority topics" cannot be derived: `topics_hint` is free text with no topic ids. The 24 h recall target is met at 2 h, and fewer requests to Google lower the risk of a block, which cannot be undone (D-004). Before launch, the team can speed up individual feeds through `sources:`.

## What happens on each tick - `python -m riffi_ingest fetch --due`

Windows Task Scheduler starts `pythonw.exe -m riffi_ingest fetch --due` every 30 minutes, hidden.

1. Open the database (first-run import of sources/topics as `fetch` does today).
2. **Due sources:** for each active source, the last time it was attempted is the latest `fetch_runs.started_at` (any status). A source is due if it was never attempted, or if `now - last >= interval - early_minutes`. `never` is never due.
3. Nothing due: record a `nothing_due` diary row, write a log line, exit 0.
4. Take the run lock (`runlock.run_lock`). If it is held, record a `busy` diary row, write a log line, exit 0.
5. Insert a diary row with outcome `running`, `started_at = now`, and the number of due sources.
6. `pipeline.run_fetch(conn, due, now=now)`. `now` is the same stamp the run's `fetch_runs` rows get, so diary and fetch history join on time.
7. Update the diary row: `finished_at`, outcome `ok` or `offline`, sources ok / failed / skipped, new items, Google refusals.
8. Any exception: update the row to `failed` with the error text, write the traceback to the log, exit 1. A killed process leaves the row at `running`. `status` reports such a row as "did not finish" once it is no longer the current run.

`fetch --all` and `fetch --source` write the same diary rows (mode `all` / `source`) and log line, so the test history is complete. Exactly one of `--all`, `--source`, `--due` is required.

## Offline runs (in `pipeline.run_fetch`)

After fetching, a run counts as **offline** if both of these are true:
- every attempted outcome (status not `skipped`) is an `error` whose `error_kind` is in `NETWORK_KINDS` or is `domain_down`, read from `fetchers/http.py` but not changed;
- those outcomes span **at least two different sites** (`domain_key`). One unreachable site alone is that site's problem.

An offline run stores nothing per source. It writes no `fetch_runs` rows and makes no health-column updates, so no `consecutive_failures` increments. It does no cleaning, grouping or tagging. Those sources therefore stay due and are retried on the next tick. `RunSummary.offline = True`.

Known limit: a tick where only the three Telegram sources are due involves one site (rsshub.app). While truly offline, those three take a strike each.

`RunSummary.google_refusals` counts outcomes from `news.google.com` with HTTP 403 / 429 or `error_kind == "blocked"`.

## The run diary - new table `engine_runs` (dangerous: `riffi_ingest/db/**`)

```sql
CREATE TABLE IF NOT EXISTS engine_runs (
    engine_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,      -- same stamp as this run's fetch_runs.started_at
    finished_at TEXT,              -- NULL while running, or if the run never finished
    mode TEXT NOT NULL,            -- due | all | source
    outcome TEXT NOT NULL,         -- running | ok | offline | failed | nothing_due | busy
    sources_due INTEGER NOT NULL DEFAULT 0,
    sources_ok INTEGER, sources_failed INTEGER, sources_skipped INTEGER,
    items_new INTEGER,
    google_refusals INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS engine_runs_started ON engine_runs (started_at);
```

The change is additive: a new table, with no change to any existing table. `CREATE TABLE IF NOT EXISTS` adds it to today's `engine.db` on the next connect, and `SCHEMA_VERSION` stays 1 (older code simply ignores the table). About 48 rows a day; kept forever as test evidence.

New `db/store.py` functions:
- `last_attempted(conn) -> dict[source_id, datetime]` (one grouped query on the existing `(source_id, started_at)` index);
- `start_engine_run`, `finish_engine_run`, `log_engine_tick` (the one-shot `nothing_due` / `busy` rows);
- `engine_runs_since(conn, since)`;
- `failing_sources(conn, at_least=3)`.

## The "is it alive?" check - `python -m riffi_ingest status`

Plain text, read-only:
- **Last run:** when (IST), mode, outcome, how long it took, sources ok / failed, new items.
- **Last 24 h:**
  - the number of runs by outcome;
  - runs that did not finish;
  - the **longest gap between ticks**, with its start and end, which is when the engine was not running.
- **Google refusals in the last 24 h.** If more than 0, a warning line.
- **Sources failing 3+ runs in a row** (BRIEF: flagged for replacement), with id, name and last error.
- An empty database or no runs yet: one line saying so, and how to start.

## Log file - `logs/engine.log`

One line per tick, appended. The line holds the IST time, mode, outcome, due / ok / failed / skipped counts, new items, Google refusals and the duration. Tracebacks go below a failed line. `*.log` is already gitignored. Every fetch writes the line as well as printing as today. Under `pythonw` there is no console, so printing must not fail when stdout is missing.

## The Windows timer - `scripts/schedule-windows.ps1`

`-Install` (default) / `-Remove`. It registers the task "Riffi ingestion engine - fetch" for the current user with these settings:
- **Action:** `<project>\.venv\Scripts\pythonw.exe -m riffi_ingest fetch --due`, working folder = the project.
- **Trigger:** once at the next :00 or :30, repeating every 30 minutes indefinitely.
- **When it runs:** only while the user is logged on (interactive; no stored password).
- **Battery and missed starts:** allowed on battery, not stopped when the laptop goes onto battery, and started as soon as possible after a missed start.
- **One at a time:** a new instance is ignored while one is running, and a run is stopped after 1 hour.

`-Install` replaces an existing task of the same name. It is run on the laptop only after the founder's yes, then verified with `Get-ScheduledTask` and one `Start-ScheduledTask` run checked through `status`.

The laptop's sleep setting is the founder's to change (a system setting). The recommendation for the test window is plugged in, with no sleep on mains power. If the laptop sleeps, the next tick catches up and the diary shows the gap.

## Files

| File | Change | Dangerous? |
|---|---|---|
| `config/schedule.yaml` | new | no |
| `riffi_ingest/scheduler.py` | new: `Schedule.load`, `interval`, `problems`, `due` | no |
| `riffi_ingest/pipeline.py` | offline rule, `RunSummary.offline` / `google_refusals` | no |
| `riffi_ingest/cli.py` | `fetch --due`, diary + log for every fetch, `status` | no |
| `riffi_ingest/db/schema.py`, `riffi_ingest/db/store.py` | `engine_runs` table + functions above | **yes** |
| `scripts/schedule-windows.ps1` | new | no (registering it on the laptop needs the founder's yes) |
| `CLAUDE.md`, `.zuko/config.json` | scheduling line: APScheduler -> Windows Task Scheduler (cron on the server later) | **yes** |
| `DECISIONS.md` | D-008 | no |
| `docs/technical-spec.md`, `docs/implementation-plan.md`, `README.md` | as-built, step 7 status, runbook | no |
| `tests/test_scheduler.py` (+ additions to `tests/test_db.py`, `tests/test_pipeline_cli.py`) | new tests | no |

## Testing

- **Due logic:**
  - never-fetched sources are due;
  - each route speed applies, and `early_minutes` is respected;
  - an override beats its route speed;
  - `never` is never due, and inactive sources are excluded;
  - the boundary at exactly `interval - early`.
- **Schedule file:** the real `config/schedule.yaml` covers every route type in the real `feeds.csv`; a bad duration gives a clear error; an override for an unknown id is reported.
- **Offline rule:**
  - two sites, all connect / DNS errors: offline, with no `fetch_runs` rows and `consecutive_failures` unchanged;
  - one site only: not offline, and the strike is counted;
  - a mix of ok and errors: not offline;
  - all skipped: not offline.
- **Diary:**
  - `--due` writes `running`, then `ok`, with counts;
  - `nothing_due` and `busy` rows are written;
  - an exception gives `failed` plus the error;
  - `--all` writes a row with mode `all`;
  - started_at equals the run's `fetch_runs.started_at`.
- **status:** empty database; normal history; a gap; a run that did not finish; Google refusals; failing sources.
- **Live (laptop, after the founder's yes):** install the task, trigger one run, `status` shows it; over the next hours, runs every 30 minutes and per-route speeds visible in `fetch_runs`.
