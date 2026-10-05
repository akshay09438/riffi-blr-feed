# Zuko handoff - Riffi Ingestion Engine

*The single source of truth for "where things stand" between sessions. `/handoff` rewrites this at the end of a session; `/start` and the SessionStart hook replay it at the beginning. Dangerous-surface status is written as a CLAIM to re-verify, never as a settled fact.*

## Last updated

5 Oct 2026 - cloud session: PR #15 (the scheduler) passed all CI checks and was merged with the founder's OK. Then the editor's ground-truth log (`log-template`, `check-log`) was built on `feat/scheduler-to20xo`. Earlier the same day, a laptop session built step 7, the scheduler, through `/zuko:build` (heavy path).

## Where things stand

- **Built and merged before this session:** fetchers for every route, cleaning + Google News resolution + blocklist, grouping into stories, keyword tagging, scoring, the database, pipeline / health / CLI / README (PRs #1-#13), and the 5 Oct handoff (#14).
- **Merged 5 Oct 2026 (PR #15):** the engine runs on its own (D-008). The timer is not installed yet (In flight 2).
  - Windows Task Scheduler starts `pythonw -m riffi_ingest fetch --due` every 30 minutes. Speeds per route are in `config/schedule.yaml`: Telegram 30 min; Google News, X/Instagram backups, publisher feeds and YouTube 2 h; page monitors 6 h.
  - A run diary (`engine_runs` table) and `data/engine.log` record every run, every check that fetched nothing, and every failure.
  - An offline run blames no source, and Google 403/429 refusals are counted.
  - `python -m riffi_ingest status` answers "is it alive?".
- **The editor's log is ready (cloud session, 5 Oct 2026):** `log-template` creates `data/ground_truth.csv` and the list of High-priority topics, and `check-log` checks each row. Run `log-template` on the laptop before day 1 of the test.
- **Not built yet:** AI tagging pass, 07:00 digest + health report, dashboard and `/api/stories`, exclusions filter, matching the log to stories + the recall report.
- **Deadline:** collecting daily by about 10-12 Oct 2026 so the two-week test finishes before launch.
- **Merge rule (D-007):** Claude may merge its own PR when every CI check is green AND no dangerous-list file changed. This PR changes dangerous files, and the founder approved each change in the session (see the PR), so it merges with the founder's OK once CI is green.

## In flight

1. ~~**PR for `feat/scheduler`.**~~ Merged 5 Oct 2026 with the founder's OK.
2. **Install the Windows timer: only after the merge, and only with the founder's explicit yes.** It is a lasting setting on the laptop.
   - `git checkout main && git pull` first. The timer runs whatever code is in this folder.
   - Then `powershell -ExecutionPolicy Bypass -File scripts\schedule-windows.ps1`.
   - Verify:
     - `Get-ScheduledTask -TaskName 'Riffi ingestion engine - fetch'`: the trigger repeats every 30 min with no end; `DisallowStartIfOnBatteries` is False and `StartWhenAvailable` is True.
     - `Start-ScheduledTask` once, then `python -m riffi_ingest status` and `data\engine.log`. The first run is a catch-up of every active source, about 12 minutes.
     - Over the next ticks: Telegram every tick, feeds every 4th, page monitors every 12th.
   - The first run on the new code adds the `engine_runs` table to `data/engine.db` (additive; backup below).
   - If registration is refused, add `-RepetitionDuration (New-TimeSpan -Days 3650)` to the trigger.
3. **While the timer is on:** keep this folder on `main`, and never try other branches here. Remove the timer first with `scripts\schedule-windows.ps1 -Remove`. That is also the emergency stop: it stops a run in progress too.
   - **Google refusals above 0 in `status`: stop the timer and ask the founder.** A Google block cannot be undone by reverting code (D-004).

## Do first next session

1. Read this file, then `BRIEF.md`, `DECISIONS.md` (wins over the brief, now up to D-008), `docs/implementation-plan.md` (open questions 1-10, drift log) and `docs/technical-spec.md` (as-built, including "Scheduler (as built)").
2. If the timer is installed: run `status` first and act on it.
   - `FAILED`, `did not finish` or `no checks at all`: run `/zuko:fix`.
   - Google refusals: stop the timer, then ask the founder.
3. Founder decisions still open (open questions 8-10 in the plan):
   - **A. Certificates.** S004, S009, S109 and S110 send an incomplete certificate chain. Proposed fix: the `truststore` library (Windows trust store; TLS stays verified). It touches `fetchers/http.py` and `requirements.txt` and is certificate configuration, so it needs explicit sign-off.
   - **B. Telegram.** rsshub.app answers 403 for S016, S101 and S102. Options: the public `t.me/s/<channel>` page, self-host RSSHub, or drop.
   - **C. README note.** On the laptop (ARM64), PyYAML 6.0.3 has no prebuilt wheel. Add a note?
4. Ranking fixes seen in the first `stories` output, one PR each:
   - Old ESPNcricinfo scorecards appear as recent.
   - `stories --hours` filters on `updated_at` (`db/store.py` is dangerous).
   - ~~Count within-run duplicates in `RunSummary`.~~ Done 5 Oct 2026 (cloud): `fetch` now prints "already stored" and "repeated within this run", so the numbers add up. Check on the next laptop run that the gap is gone.
   - Tighten the "RCB" keyword.
   - The Namma Metro timing story was split into 3 (check against `tests/test_dedupe.py` pairs).
5. Fix and re-test `feeds.csv` URLs:
   - S057 (VK, 404), S007 (KSEC, DNS), S055 / S045 / S058 (native Kannada feeds), S073, S021, S011.
   - The founder must supply S047, S107, S108 and S121.
6. Next build steps: 07:00 digest + health report (step 8), dashboard + `/api/stories` (dangerous), AI pass (open questions 1-2), exclusions filter (dangerous; needs the founder's word lists), matching the ground-truth log to stories + the recall test (the log itself is built: run `log-template` on the laptop before day 1).

## How to work here

- Live runs (`test-feeds`, `fetch`, `stories`, `status`) happen on the laptop. Cloud sessions cannot reach news sites and do not run the Zuko guard; in the cloud, check every file against the dangerous list in `CLAUDE.md` Part B yourself.
- Commands: `node .claude/hooks/py.js -m ruff check .`, `-m ruff format --check .` (it also formats Python code blocks inside Markdown), `-m pytest -q tests`.
- Tests can never touch `data/engine.db`: `tests/conftest.py` points `RIFFI_DB_PATH` at a temp file for every test.
- The founder is non-technical: use plain language, and end confirmations with "An easy way to understand this".

## Verification evidence (which checks ran, what they returned)

- Laptop, 5 Oct 2026, on `feat/scheduler`:
  - `ruff check` was clean and `ruff format --check` gave 57 files formatted.
  - `pytest -q tests` gave **345 passed** (181 before the branch).
- Database change (founder-approved):
  - Before it, a backup `data/engine-backup-2026-10-05.db` was taken with SQLite's backup API: every table count matched and `integrity_check` was ok.
  - On a backup-API copy of the real file, the new code added `engine_runs`. Every existing count was unchanged (131 / 131 / 2,177 / 2,065 / 1,368 / 14 / 100), schema_version stayed 1, and `last_attempted` returned 131 sources.
- Reviews:
  - Product check by the product-manager agent.
  - Database tests written independently by the test-author agent; the stricter rules were checked against 12 deliberately wrong versions.
  - Adversarial safety quorum: correctness safe, data safety safe, holds-up not proven safe. Its caller findings were folded into the plan and fixed.
  - A task review after every task, then a final whole-branch review: "with fixes". Those fixes were made and re-reviewed (Approved).
- Timer script:
  - It parses, and its settings were built and read back in memory.
  - `pythonw -m riffi_ingest fetch --due` ran with no window on a copy and wrote its log line and diary row.
  - **It has never been registered.** Registering is its real test (In flight 2).
- First live run, 5 Oct 2026 (before this branch):
  - `test-feeds`: 96 passed, 35 failed.
  - `fetch --all`: 115 ok, 11 errors, 5 skipped; 2,177 new items; 2,065 stories (3 High, 610 Medium, 348 Low, 1,104 Drop).

## Dangerous-surface status (claims to re-verify)

- Changed on this branch with the founder's explicit OK on 5 Oct 2026:
  - `riffi_ingest/db/schema.py` and `db/store.py`: the run diary; additive. Also a follow-up so `last_attempted` ignores future-dated stamps, and frozen value sets.
  - `tests/conftest.py`: test DB guard.
  - `CLAUDE.md` and `.zuko/config.json`: the scheduling line is now Windows Task Scheduler. The two copies are identical.
  - Approvals were recorded with `.zuko/approve.js` and cleared after each change.
- Untouched: `fetchers/http.py`, `fetchers/gnews.py`, `requirements.txt` (its header comment still mentions APScheduler; harmless, and it needs the founder to change).
- TLS verification is always on.
- `config/exclusions.yaml`, `api/stories.py`, `tagging/llm*.py` and `config/prompts/**` do not exist yet.
- Still pending from the founder: the Slack channel and member ID for `ZUKO_SLACK_WEBHOOK_INGEST` (an environment variable, never a file). `.env.example` is not written (it matches a dangerous glob).

## Open escalations

None beyond decisions A-C above and the two founder yeses in "In flight" (merge; install the timer).

## Known small follow-ups (recorded by the reviews, triaged "later")

- `status`:
  - A diary holding only quiet checks says "No runs yet".
  - A failed start while a manual run is live marks the live run dead.
  - A future-dated row can show as "Last run".
  - Plurals ("1 checks").
- Test gaps:
  - Offline detection with `domain_down` (add soon: 84 Google News feeds share one site).
  - `finish_engine_run` failing.
  - The schedule-warning lines in `status`.
- The offline rule cannot see an outage on Telegram-only ticks (3 of 4 ticks); it is documented.
- `sources_due` is not validated in `store.py` (protected).
