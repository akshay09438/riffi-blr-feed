# Zuko handoff - Riffi Ingestion Engine

*The single source of truth for "where things stand" between sessions. `/handoff` rewrites this at the end of a session; `/start` and the SessionStart hook replay it at the beginning. Dangerous-surface status is written as a CLAIM to re-verify, never as a settled fact.*

## Last updated

5 Oct 2026, evening - laptop session (Claude Desktop, Code tab): ran "Next laptop session: the checklist" steps 1-9 with the founder. The founder chose to do every code fix in the **next cloud session**. Ended early: the plan's usage limit was reached.

## Where things stand

- **Merged on `main`:** everything up to PR #25 (digest + health report, D-012), plus the scheduler (D-008, timer NOT installed: D-009), truststore (D-010), Telegram via `t.me/s/` (D-011), and the ground-truth log.
- **On branch `fix/source-addresses` (pushed, commit `58e96b8`; PR NOT opened yet: the laptop has no `gh`, and the in-app browser is not signed in to GitHub):** 11 source addresses fixed, founder-approved. Details are in the commit message (use it as the PR body). The real `data/engine.db` already holds the new addresses (`import-sources`: 0 added, 131 updated). No dangerous-list file changed, so D-007 lets Claude merge once CI is green.
- **Not built yet:** AI tagging pass, dashboard and `/api/stories`, exclusions filter, matching the log to stories + the recall report.
- **Deadline:** collecting daily by about 10-12 Oct 2026.

## Do first next session (cloud)

1. Open the PR for `fix/source-addresses` (title "Sources: fix 11 wrong or missing addresses"; body = the commit message), bind it, and merge when CI is green (D-007: no dangerous file changed).
2. Then fix each item below **in its own PR**, test first (`/zuko:fix`). Every example here was found in the real data on 5 Oct 2026:
   1. **The Polish RCB story still tags as RCB.** The real headline is `Poland cancels RCB alert over air attack on Ukraine — Interia` (S035 backup). The current excludes (`Alert RCB`, `Rządowe Centrum Bezpieczeństwa`, `Government Security Centre`) miss it: `KeywordTagger.load('config/topic_keywords.yaml').tag(...)` still gives B39, D27 and O24. Add this exact headline to `tests/test_keywords.py`, then fix it without breaking "RCB fans throng Chinnaswamy...".
   2. **Old scorecards look recent.** These are not ESPNcricinfo: they are **Cricbuzz** scorecard pages that Google News re-dates, picked up by S035's backup query `q=RCB`. Real titles: `RCB vs PWI, 31st Match, Indian Premier League 2013 - Scorecard`, `KKR vs RCB, 27th Match, Indian Premier League, 2017 - Scorecard`, `RCB vs KKR, 1st match, Indian Premier League 2008 - Scorecard`, `RCB vs PBKS, Final, Indian Premier League 2025 - Scorecard`. Also `Highlights: X vs RCB | TATA IPL 2026` videos and `... - Squads` / `- Commentary` pages. About half of today's digest top 30 is this S035 noise. Options: drop old-season scorecard/commentary titles in cleaning, and/or narrow the S035 query (needs the founder for `feeds.csv`).
   3. **The Namma Metro story split into 3.** Add these as same-story pairs in `tests/test_dedupe.py` before changing anything: `Namma Metro services to run beyond midnight on October 3` (S024+S065) / `Namma Metro Timings Extended on October 3 in Bengaluru` (S024) / `Namma Metro extends last-train timings on 3 October` (S024). A 4th copy (S061 Reddit, 5 Oct, more than 48 h later) is separate by design.
   4. **Blind page monitors.** The stored snapshots of S004 DIPR, S109 BMTC, S110 BWSSB and S048 Kannada & Culture are the same 3,588-character Kannada privacy-policy text (the karnataka.gov.in template), so they can never see a real change. S009 GBA and S008 ECI store "You need to enable JavaScript to run this app." DPAR's pages behave the same way (that is why S047 moved to Google News). Fix `fetchers/pagemonitor.py` (drop the privacy-policy block / pick the real content), or find a different URL per source.
   5. Small items:
      - `test-feeds` marks every Telegram source "missing title": `fetchers/telegram.py` leaves the title empty on purpose, but `health.REQUIRED_FIELDS` still requires one, and its fix text ("dates fall back to fetch time") is wrong. The daily health report is not affected.
      - `status` says "No gaps: the engine checked at least every 45 minutes" right after a manual fetch while `timer: off`. It should say fetching is by hand.
      - Two `test-feeds` runs in the same minute overwrite each other's report (same `%Y-%m-%d-%H%M` stamp).
      - `import-sources` keeps the old ETag when a source's address changes (S011 kept `XXXXXXXX` from PIB; harmless). `db/importers.py` is dangerous, so it needs the founder.
3. Founder decisions still open:
   - S007 KSEC: `karsec.gov.in` does not resolve at all (SERVFAIL from 8.8.8.8 and 1.1.1.1). Re-check later, or switch to a Google News search.
   - S102 TV9 Kannada Telegram: the official channel, quiet since 25 Nov 2025. Kept for the two-week evidence; decide on day 14.
   - S047 DPAR holidays (Google News search): quiet until festivals (newest story 24 Aug), so it counts against the 90% health target.
   - S015 (Cockroach Janta Party website) and S041 (BookMyShow) answer 403 to the engine.
4. The editor's log is ready: `data/ground_truth.csv` (empty, header only) and `data/ground_truth_topics.csv` (59 High-priority topics). Give both to the editor before day 1 of the test.
5. Next build steps: AI pass (open questions 1-2), dashboard + `/api/stories` (dangerous), exclusions filter (dangerous; needs the founder's word lists), matching the log to stories + the recall report.

## In flight

- `fix/source-addresses`: pushed, PR not opened, suite green (below).
- Timer: **not installed and not to be offered** (D-009). The install steps are in `README.md` and D-008 for when the founder changes D-009.

## How to work here

- Live runs (`test-feeds`, `fetch`, `stories`, `status`, `digest`) happen on the laptop. Cloud sessions cannot reach news sites and do not run the Zuko guard; in the cloud, check every file against the dangerous list in `CLAUDE.md` Part B yourself.
- The laptop has no `gh`: open PRs from a cloud session.
- Commands: `node .claude/hooks/py.js -m ruff check .`, `-m ruff format --check .`, `-m pytest -q tests`. `py` = `.venv\Scripts\python.exe -m riffi_ingest`. On Windows, set `PYTHONIOENCODING=utf-8` when printing Kannada.
- Tests can never touch `data/engine.db` (`tests/conftest.py`). `test-feeds --feeds <copy>` tests a draft copy of `feeds.csv` without touching the real one.
- The founder is non-technical: use plain language, and end confirmations with "An easy way to understand this". Ask before every fetch and before changing `feeds.csv`.

## Verification evidence (laptop, 5 Oct 2026, evening)

- `git checkout main && git pull`: fast-forwarded 43 commits to `c3f560c`.
- `pip install -r requirements.txt`: installed truststore 0.10.4.
- `pytest -q tests` on `main`: **389 passed**. On `fix/source-addresses`: first 9 failed (tests used S011 / S108 / S121 as samples); after re-pointing them (S011 → S049, same route and Official tier; explicit unfetchable rows) **389 passed**. `ruff check`: all passed. `ruff format --check`: 65 files already formatted.
- Backup: `data/engine-backup-2026-10-05-1821.db` (SQLite backup API). All 11 table counts matched (items 2,177; story_clusters 2,065; sources 131; topics 151 ...), and `integrity_check` was ok on both copies. The earlier `engine-backup-2026-10-05.db` is kept.
- `test-feeds`, certificates (D-010): S004, S009, S109, S110 all 200, **4 passed**.
- `test-feeds`, Telegram (D-011): S016 (14 posts), S101 (20), S102 (20) all 200; all marked FAIL for "missing title" (a false alarm, item 5 above); S016 is quiet since 27 Sep and S102 since 25 Nov 2025.
- `test-feeds --feeds <draft>` on the 11 new addresses: **10 passed, 1 failed** (S047 quiet).
- `fetch --all`: 126 ok, 1 not modified, 3 errors (S007 DNS, S015 403, S041 403), 1 skipped (manual). Entries 8,544 = 661 new + 2,005 already stored + 32 repeated + 5,846 older than 7 days (adds up: PR #17 confirmed). 631 new stories, 100 Google News look-ups. Took 15 min.
- `status`: 127 worked, **Google refusals: 0**, no source failing 3+ in a row. The `engine_runs` table now exists in the real database (additive, approved 5 Oct).
- `log-template`: created both files. `digest`: 2,838 items, 2,696 stories (3 High, 714 Medium, 399 Low, 1,580 Drop). Health: 110 working, 17 stale, 88 of 105 non-X sources passing (84%, target 90%). Reports are in `reports/2026-10-05/`.

## Dangerous-surface status (claims to re-verify)

- This session changed no dangerous-list file. `data/engine.db` was written only by the engine's own commands (`import-sources`, `fetch --all`), with the founder's yes, after the backup.
- TLS verification is always on (truststore, D-010).
- `config/exclusions.yaml`, `api/stories.py`, `tagging/llm*.py` and `config/prompts/**` do not exist yet.
- Still pending from the founder: the Slack channel and member ID for `ZUKO_SLACK_WEBHOOK_INGEST`; `.env.example` is not written.

## Open escalations

The founder decisions in "Do first" step 3. Nothing dangerous is waiting.

## Known small follow-ups (recorded by reviews, triaged "later")

- `status`: a failed start while a manual run is live marks the live run dead; a future-dated row can show as "Last run".
- Test gaps: offline detection with `domain_down`, `finish_engine_run` failing, the schedule-warning lines in `status`.
- `sources_due` is not validated in `store.py` (protected).
- The digest's keyword tags misfire on some names (e.g. "Raghuvanshi ... long layoff" → layoffs topics, "Yash Rathod" → D14), which is open question 7; the AI pass is meant to absorb it.
