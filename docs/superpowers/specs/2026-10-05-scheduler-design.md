# Scheduler design - the engine runs on its own (BRIEF step 7)

*Approved by the founder on 5 Oct 2026 in a /zuko:build session. Decisions taken: Google News every 2 h during the two-week test; Windows' own timer (Task Scheduler) instead of APScheduler.*

*Updated to as-built after the safety review, 5 Oct 2026. Three independent reviewers and an independent test author went over the change; where the first design and the code differ, this page now describes the code. The timer itself is written and tested but not installed on the laptop yet: that waits for the merge and the founder's separate yes.*

## Why

The two-week source test has to start by about 10-12 Oct 2026. The real job here is **14 days of trustworthy, unattended fetching, where every gap has a known cause**. A gap might mean the laptop was asleep, the internet was down, or the source itself failed. If those causes are told apart, the day-14 keep/fix/drop verdicts are fair to each source. Before this change the engine only ran when a person typed `fetch --all`.

Who it serves now: the two-week test (and the editor whose log it is scored against), and the founder, who needs to see in seconds that it is alive. The content team and seed agent benefit later (step 8 reads what this collects).

**Did it work:** without anyone typing anything, a check happens every 30 minutes. Each source is fetched at its speed: Telegram 30 min, feeds 2 h, page monitors 6 h. A Wi-Fi-off period shows as "offline" and blames no source.
**Did it help:** in the first 48 h, these hold:
- no manual runs are needed;
- no two runs overlap;
- each run finishes in under 15 minutes;
- Google refusals stay at 0;
- every gap in the run diary is explained by the laptop being off or asleep.

These are checked live after the timer is installed.

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

Durations are `<n>m`, `<n>h` or `never`. Loading fails with a clear message naming the file on a bad value. Anything faster than 30m behaves like 30m, because the timer's beat is 30 minutes. `Schedule.problems` lists any route type without a speed (its sources would never be fetched) and any override for an unknown or inactive source. `fetch --due` prints and logs each problem as a warning, `status` prints them too, and a test checks the real `schedule.yaml` against the real `feeds.csv`.

This departs from the brief's speeds, which put High-priority Google News queries and the priority X feeds at 30 min. The departure is recorded as **D-008**. The brief's list of "Google News feeds tied to High-priority topics" cannot be derived: `topics_hint` is free text with no topic ids. The 24 h recall target is met at 2 h, and fewer requests to Google lower the risk of a block, which cannot be undone (D-004). Before launch, the team can speed up individual feeds through `sources:`.

## What happens on each check - `python -m riffi_ingest fetch --due`

Windows Task Scheduler starts `pythonw.exe -m riffi_ingest fetch --due` every 30 minutes, hidden. Exactly one of `--all`, `--source`, `--due` is required.

1. Open the database (the first ever run imports sources and topics, as `fetch` always did).
2. **Due sources:** for each active source, the last time it was attempted is the latest `fetch_runs.started_at` (any status). A source is due if it was never attempted, or if `now - last >= interval - early_minutes`. `never` is never due. `last_attempted(conn, now)` ignores fetch stamps dated after `now`: they come from a laptop clock that was wrong at that moment, and counting them would make a source due at every check until real time caught up. So after a clock jump a source is fetched once, then at its normal speed. (`due()` also treats a last attempt in the future as due, as a second guard.)
3. Nothing due: write a `nothing_due` diary row and a log line, print "Nothing is due yet.", exit 0.
4. Take the run lock (`runlock.run_lock`, the file `fetch.lock` beside the database). If it is held: with `--due`, write a `busy` diary row and a log line, exit 0; with `--all` or `--source`, stop with an error and no row.
5. Insert a diary row with outcome `running`, `started_at = now`, and the number of due sources.
6. `pipeline.run_fetch(conn, due, now=now)`. `now` is the same stamp the run's `fetch_runs` rows get, so diary and fetch history join on time.
7. Close the diary row: `finished_at`, outcome `ok` or `offline`, sources ok / failed / skipped, new items, Google refusals. (`ok` counts both `ok` and `not_modified` fetches.)
8. Any exception: the row is closed as `failed` with the reason in `error`, the traceback goes to the log, and the command exits 1. Ctrl+C closes the row as `failed` too (the reason is `KeyboardInterrupt`). Only a hard kill (power cut, killed process) leaves the row at `running`.
9. A check that fails **before a run can start** (an unreadable `schedule.yaml`, a database that stays locked for 30 s, any other failure while starting the run) writes a one-shot `failed` row, if the database answers, and a log line with the traceback. `status` then does not mistake it for the laptop being off.

The log line is always written first. The diary row is best-effort: if the database refuses it, the log gets a `warning` line and the fetch is never hidden or stopped.

`fetch --all` and `fetch --source` write the same diary rows and log line (mode `all` / `source`), so the test history is complete.

## Offline runs (in `pipeline.run_fetch`)

After fetching, a run counts as **offline** (`pipeline.looks_offline`) if both of these are true:
- every attempted outcome (status not `skipped`) is an `error` whose `error_kind` is in `UNREACHABLE_KINDS`: timeout, dns, connect (`NETWORK_KINDS`, read from `fetchers/http.py` but not changed) or `domain_down`;
- those outcomes span **at least two different sites** (`domain_key`). One unreachable site alone is that site's problem.

An offline run returns right after fetching. It stores nothing per source: no `fetch_runs` rows, no health-column updates (so no `consecutive_failures` increments), no cleaning, grouping or tagging, no Google link cache. Those sources therefore stay due and are retried at the next check. `RunSummary.offline = True`, the diary row says `offline`, and `fetch` prints "No internet".

**Known limit:** a check where only the three Telegram sources (S016, S101, S102) are due involves one site (rsshub.app), so it cannot be recognised as offline. While the internet is really down, those three take a strike each.

**Google refusals** (`pipeline.google_refusals`, `RunSummary.google_refusals`) are Google News feed fetches (`news.google.com`) answered with HTTP 403 or 429. The first design also counted `error_kind == "blocked"`; that kind only means a link to X, Instagram or WhatsApp was refused, so it never happens for news.google.com and is not counted. Link look-ups are not counted. The count is taken before the offline check, so offline runs report it too.

## The run diary - new table `engine_runs` (dangerous: `riffi_ingest/db/**`)

```sql
CREATE TABLE IF NOT EXISTS engine_runs (
    engine_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,      -- the run's clock: equal to its fetch_runs.started_at
    finished_at TEXT,              -- NULL while running, or if the run never finished (killed, crashed)
    mode TEXT NOT NULL,            -- due (the timer) | all | source
    outcome TEXT NOT NULL,         -- running | ok | offline | failed | nothing_due | busy
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

The change is additive: a new table, with no change to any existing table. `CREATE TABLE IF NOT EXISTS` adds it to an existing `engine.db` on the next connect, and `SCHEMA_VERSION` stays 1 (older code simply ignores the table). About 48 rows a day; kept forever as test evidence. **A backup of `engine.db` was taken before the change** (`data/engine-backup-2026-10-05.db`, gitignored; row counts and integrity checked).

Modes and outcomes are free text in the table, so `db/store.py` checks them in Python before writing, against fixed lists (`DIARY_MODES`, `FINISHED_OUTCOMES`, `TICK_OUTCOMES`, all frozensets). A typo would otherwise mislead `status` and the day-14 report quietly.

New `db/store.py` functions:
- `last_attempted(conn, now=None) -> dict[source_id, datetime]` (one index lookup per source on the existing `(source_id, started_at)` index; with `now`, stamps after it are ignored);
- `start_engine_run` (a `running` row for `due`, `all` or `source`);
- `finish_engine_run`, which closes a row **only while it is still `running`** and raises otherwise (an unknown id, a run already finished, a one-shot row), so the diary never says something that did not happen;
- `log_engine_tick`, the one-shot rows: `nothing_due`, `busy`, and `failed` (a check that could not start, with its error), written with the same start and finish time;
- `engine_runs_since(conn, since)`, `last_engine_run(conn)` (the latest row that is not `nothing_due` or `busy`), `first_engine_run_at(conn)`;
- `failing_sources(conn, at_least=3)`.

## The "is it alive?" check - `python -m riffi_ingest status`

Plain text. It writes nothing to the diary or the data; the only side effect is trying the run lock for an instant.
- **Last run:** the latest run that fetched or tried to: when (IST), how long ago, mode, outcome, how long it took, sources ok / failed / skipped and new items, or the reason it failed or why it was offline. A row still marked `running` is "running now" if a fetch holds the run lock at this moment, and otherwise "did not finish (stopped or crashed)".
- **Last 24 h:**
  - the number of checks by outcome (a dead `running` row counts as "did not finish");
  - the **longest gap with no check**, with its start and end, shown when it is over 45 minutes, otherwise "No gaps". A gap is measured from the **end** of one check to the start of the next, so a long run is not mistaken for the laptop being off. It is counted from the **later** of 24 hours ago and the diary's first row, so a diary that is only an hour old does not claim 23 hours of silence, and an outage that began before the window still counts;
  - a warning if any diary rows are **dated in the future** (the laptop's clock jumped, so the times may be wrong);
  - no checks at all in 24 hours is said plainly.
- **Google refusals in the last 24 h.** If more than 0, a warning line to stop and ask before fetching more.
- **Sources failing 3+ runs in a row** (BRIEF: flagged for replacement), active sources only, with id, name, count and last status.
- The same `schedule.yaml` warnings as `fetch --due`, or a warning that the file cannot be read.
- No run in the diary yet (checks that found nothing due do not count as runs): one line saying so, and how to start.

## Log file - `engine.log` beside the database (`data/engine.log`)

One line per check, appended: the IST time, mode, outcome, then due / ok / failed / skipped counts, new items, Google refusals and minutes taken. A `nothing_due` check has only the outcome; a `busy` one adds how many sources were due. A traceback goes below a `failed` line, and a `warning` line records schedule problems and diary rows that could not be written. The file sits in the database's folder, like the run lock `fetch.lock`, not in a separate `logs/` folder, so a test database in a temporary folder never writes into the real log. `data/` is gitignored. Every fetch writes the line as well as printing as before. Under `pythonw` there is no console; `typer.echo` does nothing when there is no console, so printing cannot crash the run.

## The Windows timer - `scripts/schedule-windows.ps1`

`-Remove` removes it; without it the script installs. It registers the task "Riffi ingestion engine - fetch" for the current user with these settings:
- **Action:** `<project>\.venv\Scripts\pythonw.exe -m riffi_ingest fetch --due`, working folder = the project.
- **Trigger:** once at the next :00 or :30, repeating every 30 minutes with no end.
- **When it runs:** only while the user is logged on (interactive; no stored password).
- **Battery and missed starts:** allowed on battery, not stopped when the laptop goes onto battery, and started as soon as possible after a missed start.
- **One at a time:** a new instance is ignored while one is running, and a run is stopped after 1 hour.

Installing replaces an existing task of the same name. **It has not been run yet.** It is run on the laptop only after this branch is merged and the founder says yes, then verified with `Get-ScheduledTask` and one `Start-ScheduledTask` run checked through `status`.

The laptop's sleep setting is the founder's to change (a system setting). The recommendation for the test window is plugged in, with no sleep on mains power. If the laptop sleeps, the next check catches up and the diary shows the gap.

## Files

| File | Change | Dangerous? |
|---|---|---|
| `config/schedule.yaml` | new | no |
| `riffi_ingest/scheduler.py` | new: `Schedule.load`, `interval`, `problems`, `due` | no |
| `riffi_ingest/pipeline.py` | offline rule, `RunSummary.offline` / `google_refusals` | no |
| `riffi_ingest/cli.py` | `fetch --due`, diary + log for every fetch, `status` | no |
| `riffi_ingest/runstatus.py` | new: what `status` prints | no |
| `riffi_ingest/db/schema.py`, `riffi_ingest/db/store.py` | `engine_runs` table + functions above | **yes** (founder-approved 5 Oct 2026) |
| `tests/conftest.py` | every test gets a temporary database through `RIFFI_DB_PATH`, so no test can write into `data/engine.db` | **yes** (founder-approved 5 Oct 2026) |
| `scripts/schedule-windows.ps1` | new | no (registering it on the laptop needs the founder's yes) |
| `CLAUDE.md`, `.zuko/config.json` | scheduling line: APScheduler -> Windows Task Scheduler (the server's own timer later) | **yes** (founder-approved; commit 46faf61) |
| `DECISIONS.md` | D-008 | no |
| `docs/technical-spec.md`, `docs/implementation-plan.md`, `README.md` | as-built, step 7 status, runbook | no |
| `tests/test_scheduler.py`, `tests/test_runstatus.py` (new), additions to `tests/test_db.py`, `tests/test_pipeline_cli.py` | new tests | no |

## Testing

All offline, with a fake network and temporary databases.
- **Due logic:**
  - never-fetched sources are due;
  - each route speed applies, and `early_minutes` is respected;
  - an override beats its route speed;
  - `never` is never due, and a route type with no speed is never due;
  - a last attempt dated in the future is due;
  - after a clock jump a source is fetched once, then keeps its speed.
- **Schedule file:** the real `config/schedule.yaml` covers every route type in the real `feeds.csv`; a bad duration gives a clear error; an override for an unknown id is reported; a broken or missing file leaves a `failed` diary row.
- **Offline rule:**
  - two sites, all connect / DNS errors: offline, with no `fetch_runs` rows and `consecutive_failures` unchanged;
  - one site only: not offline, and the strike is counted;
  - a site that answers means the internet is up;
  - Google refusals (403 / 429 from news.google.com) are counted.
- **Diary:**
  - `--due` writes `running`, then `ok`, with counts, and the same start stamp as `fetch_runs`;
  - `nothing_due` and `busy` rows are written;
  - an exception gives `failed` plus the error, and Ctrl+C closes the row as `failed` too;
  - runs by hand (`--all`, `--source`) write rows with their mode;
  - a run that cannot start, and a database that will not open, still leave a log line (and a `failed` row where the database answers);
  - unknown modes and outcomes are refused, a row can only be finished while `running`, and an existing database gains the table with its data untouched.
- **status:** before any run; a healthy day; a gap, including one that began before the window; a long run is not a gap; a run that never finished versus one holding the lock; failed and offline runs; a check that could not start; Google refusals; rows dated in the future; failing sources; no checks in 24 hours; the command itself.
- **Safety net:** a test checks that no test uses the real database.
- **Live (laptop, after the merge and the founder's yes):** install the task, trigger one run, `status` shows it; over the next hours, a check every 30 minutes and per-route speeds visible in `fetch_runs` (Telegram at every check, other feeds at every 4th, page monitors at every 12th).
