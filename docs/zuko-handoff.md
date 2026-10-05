# Zuko handoff - Riffi Ingestion Engine

*The single source of truth for "where things stand" between sessions. `/handoff` rewrites this at the end of a session; `/start` and the SessionStart hook replay it at the beginning. Dangerous-surface status is written as a CLAIM to re-verify, never as a settled fact.*

## Last updated

5 Oct 2026, late evening - laptop session (Claude Desktop, Code tab). The founder asked for: pull `main`, install, test, back up the database, `import-sources`, `test-feeds` on S007/S015/S041, `fetch --all`, `digest`, and the top stories of the last 24 hours. No timer. All done. No code changed; this branch (`docs/handoff-2026-10-05-evening`) changes only this file, `docs/implementation-plan.md` and `docs/technical-spec.md`.

## Where things stand

- **Merged on `main` (`80f1835`):** everything up to PR #32. That includes the 11 source-address fixes (#26), S007/S015/S041 on Google News (D-013, #27), dropping old cricket match pages (#28), the three small follow-ups (#29), the page-monitor pruning (#30/#31) and the grouping fix for dates and word forms (#32).
- **Checked on real data tonight:** the Google News searches for S007, S015 and S041 work. The Polish "RCB alert" headline no longer tags as RCB. Match pages are dropped (8 tonight). `status` now says "fetching is by hand for now, D-009". `test-feeds` report names now include seconds.
- **Not working on real data: the page-monitor fix (PR #31).** Six government pages are still blind. Details are in "Do first", item 2.
- **Leftovers in the database (not new bugs):** the 13 old scorecards, highlight videos and the Polish story in tonight's top 30 were all stored by the two earlier runs on 5 Oct (14:14 and 18:43 IST), before the fixes landed. The same goes for the three Namma Metro copies and the three rain copies. Nothing re-cleans stored items. They leave the 24-hour window after about 18:43 IST on 6 Oct, so a digest run on the morning of 6 Oct will still show them. Removing them by hand would mean editing `data/engine.db` (dangerous), so the founder was told they will age out.
- **Not built yet:** AI tagging pass, dashboard and `/api/stories`, exclusions filter, matching the log to stories, and the recall report.
- **Deadline:** collecting daily by about 10-12 Oct 2026.

## Do first next session

**Cloud:**
1. Open the PR for `docs/handoff-2026-10-05-evening` (title "Handoff: laptop run of the cloud fixes, 5 Oct evening"). Bind it and merge when CI is green. It changes docs only, so D-007 allows Claude to merge.
2. **Fix the blind page monitors, test first (`/zuko:fix`).** Two separate faults:
   1. **304 hides a blind snapshot (S008 ECI, S009 GBA).** Tonight both answered 304 Not Modified to the conditional GET. S008 has done so since 18:43 IST; S009 was 200 then and 304 tonight. So the new rules never saw their pages, and the stored text is still "You need to enable JavaScript to run this app.". `monitor_page` already skips validators when there is no snapshot (`fetchers/pagemonitor.py`, around line 279). It should also skip them when the stored snapshot is blind (`was_blind`-style: only JavaScript notices, or policy text). This can be fixed in the cloud without the real page.
   2. **Pruning misses the real karnataka.gov.in markup (S004 DIPR, S048 Kannada & Culture, S109 BMTC, S110 BWSSB).** All four answered 200 tonight with the new code and still stored the same 3,588-character Kannada privacy policy (hash `eb00a106...`, text starting "ಗೌಪ್ಯತೆ ನೀತಿ: (ವೆಬ್ಸೈಟ್ ಯಾವುದೇ ವೈಯಕ್ತಿಕ ಮಾಹಿತಿಯನ್ನು ಸಂಗ್ರಹಿಸದಿದ್ದಾಗ)..."). The fixture `tests/fixtures/karnataka_gov_template.html` does not match the real page. `is_policy_title` already accepts that spelling and the trailing colon. A guess, to confirm on the real HTML: the real page prints "ಗೌಪ್ಯತೆ ನೀತಿ:" inline at the start of a paragraph, not as a heading, inside a container with no policy id or class, so no pruning rule fires. **It needs the real HTML:** the next laptop session saves it (laptop item 1). Do not guess the markup.
3. Then the build: the AI pass (open question 2 needs the founder), dashboard and `/api/stories` (dangerous), exclusions filter (dangerous; needs the founder's word lists), and matching the log to stories plus the recall report.

**Laptop (ask the founder before every fetch):**
1. Save the raw HTML of S004's page (one polite request) and one of S048/S109/S110 into `tests/fixtures/`, so the cloud can fix the pruning against the real thing. Push it on a branch; the laptop has no `gh`.
2. After the page-monitor fix merges: `git pull`, run the suite, back up the database, `test-feeds --source S004 --source S048 --source S109 --source S110 --source S008 --source S009`, then check `page_snapshots` holds real page text, not the policy or the JavaScript notice.
3. Give the editor `data/ground_truth.csv` and `data/ground_truth_topics.csv` before day 1 of the test.

## In flight

- `docs/handoff-2026-10-05-evening`: committed and pushed, PR not opened (no `gh` on the laptop). Docs only. Suite green on `main` (below).
- Timer: **not installed and not to be offered** (D-009). The install steps are in `README.md` and D-008 for when the founder changes D-009.

## How to work here

- Live runs (`test-feeds`, `fetch`, `stories`, `status`, `digest`) happen on the laptop. Cloud sessions cannot reach news sites and do not run the Zuko guard. In the cloud, check every file against the dangerous list in `CLAUDE.md` Part B yourself.
- The laptop has no `gh`, and the in-app browser is not signed in to GitHub, so open PRs from a cloud session.
- Commands: `node .claude/hooks/py.js -m ruff check .`, `-m ruff format --check .`, `-m pytest -q tests`. `py` = `.venv\Scripts\python.exe -m riffi_ingest`. On Windows, set `PYTHONIOENCODING=utf-8` when printing Kannada.
- Reading the real database for evidence: open it read-only (`sqlite3.connect('file:data/engine.db?mode=ro', uri=True)`) from a script file in the scratchpad. Inline Python through the shell loses backslashes.
- Tests can never touch `data/engine.db` (`tests/conftest.py`). `test-feeds --feeds <copy>` tests a draft copy of `feeds.csv` without touching the real one.
- The founder is non-technical: use plain language, and end confirmations with "An easy way to understand this". Ask before every fetch and before changing `feeds.csv`.

## Verification evidence (laptop, 5 Oct 2026, 19:55-20:30 IST)

- `git checkout main && git pull`: fast-forwarded `c3f560c` → `80f1835` (PRs #26-#32).
- `pip install -r requirements.txt`: exit 0, nothing new.
- `pytest -q tests`: **410 passed, 2 xfailed** in 28.75 s. The 2 xfails are the strict expected failures for "Namma Metro services to run beyond midnight on October 3" against each of the other two titles (PR #32). `ruff check`: all checks passed. `ruff format --check`: 65 files already formatted.
- Backup: `data/engine-backup-2026-10-05-2003.db` (SQLite backup API, 13,864,960 bytes). All 11 table counts matched (items 2,838; story_clusters 2,696; fetch_runs 262; item_topics 1,588; sources 131; topics 151 ...). `integrity_check` was ok on both copies. The earlier backups are kept.
- `import-sources`: 0 added, 131 updated, 0 marked inactive.
- `test-feeds --source S007 --source S015 --source S041`: **3 passed**. S007 100 items, newest 3 Oct 19:30 IST; S015 102, newest 5 Oct 18:22; S041 68, newest 5 Oct 05:22. Report: `reports/test-feeds/2026-10-05-200319.md`. Stored after the fetch:
  - S007: 2 Karnataka voter-roll (SIR) stories.
  - S015: 3 stories on the CJP's 10 October protest, from regular news sites.
  - S041: 2 Diljit Dosanjh tour-ticket stories.
- `fetch --all` (started 20:06 IST, 15 min): 127 ok, 3 not modified, 1 skipped (S112 manual), **0 errors**. Entries 8,882 = 194 new + 2,582 already stored + 5 repeated + 6,093 older than 7 days + 8 match-record pages (adds up). 173 new stories, 13 grown; 29 tagged, 20 unmatched local; 100 Google News look-ups; scored 17 Medium, 12 Low, 157 Drop.
- `status`: 130 worked, 0 failed, 1 skipped; **Google refusals: 0**; no source failing 3+ in a row; "fetching is by hand for now, D-009".
- `digest`: 3,032 items, 2,869 stories (3 High, 728 Medium, 411 Low, 1,727 Drop); 1,142 in the CSV. Health: 113 working, 17 stale, 0 failing 3+, 52 not useful in 7 days (under 7 days of history). **91 of 105 non-X sources passing (87%, target 90%)**, up from 84%. Reports are in `reports/2026-10-05/`.
- `KeywordTagger` on "Poland cancels RCB alert over air attack on Ukraine — Interia" → no topics. On "RCB fans throng Chinnaswamy for the title celebration" → D27, O24, B39 (still right).
- Page monitors, read from `fetch_runs` and `page_snapshots` after the fetch: see "Do first" cloud item 2. Also worth a look: S029 IMD's stored text begins "Met Centre Raipur" under the title "Karnataka". Check it is the right page. S025 Namma Metro stores 3,499 characters of BMRCL's Kannada introduction, which may never change.

## Dangerous-surface status (claims to re-verify)

- This session changed no dangerous-list file. `data/engine.db` was written only by the engine's own commands (`import-sources`, `fetch --all`), with the founder's yes, after the backup. Every evidence query opened it read-only.
- TLS verification is always on (truststore, D-010). S004, S009, S109 and S110 answered over verified TLS tonight.
- `config/exclusions.yaml`, `api/stories.py`, `tagging/llm*.py` and `config/prompts/**` do not exist yet.
- Still pending from the founder: the Slack channel and member ID for `ZUKO_SLACK_WEBHOOK_INGEST`. `.env.example` is not written.

## Open escalations

Nothing dangerous is waiting. Founder decisions still open:
- Open question 2: when the AI pass runs.
- The exclusions word lists.
- `import-sources` keeps the old ETag when a source's address changes (S011 kept PIB's; harmless so far). The fix is in `db/importers.py`, which is dangerous.
- Certificates on the future server (open question 11).
- The timer stays off (D-009) until the founder says otherwise.

## Known small follow-ups (recorded by reviews, triaged "later")

- `status`: a failed start while a manual run is live marks the live run dead; a future-dated row can show as "Last run".
- Test gaps: offline detection with `domain_down`, `finish_engine_run` failing, the schedule-warning lines in `status`.
- `sources_due` is not validated in `store.py` (protected).
- The digest's keyword tags misfire on some names (e.g. "Raghuvanshi ... long layoff" → layoffs topics, "Yash Rathod" → D14). This is open question 7; the AI pass is meant to absorb it. Tonight O02 (Siddaramaiah/Shivakumar) tagged 9 of the top 13 stories, as open question 7 predicted.
- Stories split by wording, for the AI pass: the "beyond midnight" Namma Metro title (known), and the rain coverage on 5 Oct ("Heavy rain throws Bengaluru into chaos: Waterlogging grips key roads" / "Bengaluru rain chaos: City battles waterlogging, traffic snarls..." / "Bengaluru rains trigger waterlogging, traffic snarls; Krishna Byre Gowda orders 24x7 control rooms"). They are arguably different angles, so don't force them by title.
