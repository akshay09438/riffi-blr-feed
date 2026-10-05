# Zuko handoff - Riffi Ingestion Engine

*The single source of truth for "where things stand" between sessions. `/handoff` rewrites this at the end of a session; `/start` and the SessionStart hook replay it at the beginning. Dangerous-surface status is written as a CLAIM to re-verify, never as a settled fact.*

## Last updated

5 Oct 2026, late evening - laptop session (Claude Desktop, Code tab). The founder asked for: pull `main`, install, test, back up the database, `import-sources`, `test-feeds` on S007/S015/S041, `fetch --all`, `digest`, and the top stories of the last 24 hours. No timer. All done. Then, with the founder's yes, saved the real S004 and S109 pages as test fixtures and diagnosed the page-monitor bug offline. Then, also with the founder's yes, found a better page for DIPR press releases and re-pointed S004 to the CM's English news page (D-014). No engine code changed. This branch (`docs/handoff-2026-10-05-evening`) changes this file, `docs/implementation-plan.md`, `docs/technical-spec.md`, `DECISIONS.md` (D-014), the S004 row of `feeds.csv`, and three new files in `tests/fixtures/`.

## Where things stand

- **Merged on `main` (`80f1835`):** everything up to PR #32. That includes the 11 source-address fixes (#26), S007/S015/S041 on Google News (D-013, #27), dropping old cricket match pages (#28), the three small follow-ups (#29), the page-monitor pruning (#30/#31) and the grouping fix for dates and word forms (#32).
- **Checked on real data tonight:** the Google News searches for S007, S015 and S041 work. The Polish "RCB alert" headline no longer tags as RCB. Match pages are dropped (8 tonight). `status` now says "fetching is by hand for now, D-009". `test-feeds` report names now include seconds.
- **Not working on real data: the page-monitor fix (PR #31).** Six government pages are still blind. Details are in "Do first", item 2.
- **Leftovers in the database (not new bugs):** the 13 old scorecards, highlight videos and the Polish story in tonight's top 30 were all stored by the two earlier runs on 5 Oct (14:14 and 18:43 IST), before the fixes landed. The same goes for the three Namma Metro copies and the three rain copies. Nothing re-cleans stored items. They leave the 24-hour window after about 18:43 IST on 6 Oct, so a digest run on the morning of 6 Oct will still show them. Removing them by hand would mean editing `data/engine.db` (dangerous), so the founder was told they will age out.
- **Not built yet:** AI tagging pass, dashboard and `/api/stories`, exclusions filter, matching the log to stories, and the recall report.
- **Deadline:** collecting daily by about 10-12 Oct 2026.

## Do first next session

**Cloud:**
1. Open the PR for `docs/handoff-2026-10-05-evening` (title "Handoff: laptop run of the cloud fixes, 5 Oct evening; S004 to the CM's news page"). Bind it and merge when CI is green. It changes docs, three fixtures and one founder-approved `feeds.csv` row, and no dangerous-list file, so D-007 allows Claude to merge.
2. **Fix the blind page monitors, test first (`/zuko:fix`).** Two separate faults:
   1. **304 hides a blind snapshot (S008 ECI, S009 GBA).** Tonight both answered 304 Not Modified to the conditional GET. S008 has done so since 18:43 IST; S009 was 200 then and 304 tonight. So the new rules never saw their pages, and the stored text is still "You need to enable JavaScript to run this app.". `monitor_page` already skips validators when there is no snapshot (`fetchers/pagemonitor.py`, around line 279). It should also skip them when the stored snapshot is blind (`was_blind`-style: only JavaScript notices, or policy text). This can be fixed in the cloud without the real page.
   2. **Pruning misses the real karnataka.gov.in markup (S004 DIPR, S048 Kannada & Culture, S109 BMTC, S110 BWSSB).** All four answered 200 tonight with the new code and still stored the same 3,588-character Kannada privacy policy (hash `eb00a106...`). **The real pages are now saved** (founder's yes, one request each, 5 Oct ~20:45 IST): `tests/fixtures/karnataka_gov_real_S004_dipr.html` and `tests/fixtures/karnataka_gov_real_S109_bmtc.html`. Both give exactly that hash through today's `page_lines`, so they reproduce the bug offline. What they show (diagnosed offline; no engine code changed):
      - Every page in this template has about 21 hidden Bootstrap modals (`div.modal.fade`), the same ids on both sites. The template policy modals and their real headings are:
        - `#fmyModal3` ಕೃತಿಸ್ವಾಮ್ಯ ನೀತಿ (copyright)
        - `#fmyModal4` ಹೈಪರ್ಲಿಂಕಿಂಗ್ ನೀತಿ (hyperlinking)
        - `#fmyModal5` ಭದ್ರತಾ ನೀತಿ (security)
        - `#fmyModal6` ನಿಯಮ ಮತ್ತು ಶರತ್ತುಗಳು (terms; note ಶ, not ಷ)
        - `#fmyModal7` ಗೌಪ್ಯತೆ ನೀತಿಗಳು (privacy, plural)
        - `#fmyModal8` ಸಹಾಯ (help)
        - `#fmyModal9` and `#screen_reader_kn` ಸ್ಕ್ರೀನ್ ರೀಡರ್ ಪ್ರವೇಶ (screen reader)

        **`is_policy_title` rejects every one of these headings**, so none is pruned. `#myModal1`-`#myModal11` are link lists (useful sites, e-governance, downloads ...), and `#eventModal` is empty.
      - Accepting only the plural (tried by patching the regex in a scratch script) removes the privacy modal, but trafilatura then takes the terms modal (`#fmyModal6`) instead. Fixing one heading at a time just moves the problem.
      - **The only content that changes is also in a hidden modal:** `#newsModal` "ಇತ್ತೀಚಿನ ಸುದ್ದಿಗಳು" (latest news). DIPR has 9 entries, and BMTC has 84 plus `#exampleModal` "Quick Announcements". There is no visible copy outside the modals. Dropping every modal leaves only the menu and the minister's intro (788 / 1,134 chars), which never change. So "drop all hidden modals" is wrong. The likely shape of the fix is to watch `#newsModal` (plus `#exampleModal`) on this template and drop the rest; the design is the cloud's call.
      - Each news line ends in a relative age ("Student Pass 4 months ago", "... 2 years ago"). Those tails change by themselves, so strip a trailing "N minutes/hours/days/months/years ago" (and the Kannada form, if any) or every page fires a false "updated" item each month.
      - **DIPR's homepage was not where its press releases go** (its newest news entry says "2 years ago"), so **S004 now watches `https://cm.karnataka.gov.in/en`** (D-014, founder's yes). BMTC's newest entry is "4 months ago", so it is slow but alive.
      - **The third fixture, `tests/fixtures/karnataka_gov_real_S004_cm_en.html`** (the CM's English page, saved 5 Oct ~21:15 IST), is the same template with a different layout, so the fix must handle both:
        - Its news list (152 English headlines, each with a dateline such as "Bengaluru, October 02, 2026") is **visible, not in a modal**.
        - Its policy modals have other ids and English headings: `#myModalf3` Copyright Policy, `#myModalf4` Hyperlinking Policy, `#myModalf5` Security Policy, `#myModalf6` Terms & Conditions, `#myModalf7` Privacy Policies, `#myModalf8` Help, `#myModalf9` Screen Reader Access. `is_policy_title` accepts only the first, second and fourth.
        - Today's `page_lines` gives the English privacy policy (hash `07ab1574...`, "We collect no personal information...").
        - With every modal dropped, trafilatura picks a block of Kannada photo-gallery captions (11,207 chars) instead of the English headlines.

        So "trafilatura's main text" is the wrong tool for this template. Pick the news list itself: `#newsModal` on DIPR/BMTC-style pages, and the visible headline list on the CM page.
3. Then the build: the AI pass (open question 2 needs the founder), dashboard and `/api/stories` (dangerous), exclusions filter (dangerous; needs the founder's word lists), and matching the log to stories plus the recall report.

**Laptop (ask the founder before every fetch):**
1. Done 5 Oct: the real DIPR (old S004), BMTC (S109) and CM English (new S004) pages are saved in `tests/fixtures/` on this branch.
2. After this branch merges: `git pull`, then `import-sources` (with the founder's yes, after a backup), so the database takes S004's new address. **Until then `data/engine.db` still holds the old DIPR address.** Note the known `import-sources` bug: it keeps the old ETag when an address changes. The CM server should simply ignore an ETag it didn't issue, but check that S004's first fetch answers 200.
3. After the page-monitor fix merges: `git pull`, run the suite, back up the database, `test-feeds --source S004 --source S048 --source S109 --source S110 --source S008 --source S009`, then check `page_snapshots` holds real page text, not the policy or the JavaScript notice.
4. Give the editor `data/ground_truth.csv` and `data/ground_truth_topics.csv` before day 1 of the test.

## In flight

- `docs/handoff-2026-10-05-evening`: committed and pushed, PR not opened (no `gh` on the laptop). Docs, D-014 with its one `feeds.csv` row, and three HTML fixtures (no test uses them yet). Suite green (below).
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
- Optional: add `karnatakavarthe.org` to `blocklist.csv` (dangerous file). The domain is no longer DIPR's and now serves casino-spam links (D-014). No feed links to it today, so it is not urgent.
- Open question 2: when the AI pass runs.
- The exclusions word lists.
- `import-sources` keeps the old ETag when a source's address changes (S011 kept PIB's; harmless so far). The fix is in `db/importers.py`, which is dangerous.
- Certificates on the future server (open question 11).
- The timer stays off (D-009) until the founder says otherwise.

## Known small follow-ups (recorded by reviews, triaged "later")

- **An unidentified flaky test.** After the D-014 edits (around 21:20 IST, 5 Oct), one full run gave `1 failed, 409 passed, 2 xfailed` in 40.3 s (slower than usual). The next five runs, with no change in between, were all `410 passed, 2 xfailed`. Its name was not captured, and the passing runs cleared `.pytest_cache/.../lastfailed`. The edits touched only `feeds.csv` (one row), docs and an unused fixture, and that row reads back correctly through `load_sources`. A timing-sensitive test is the likely suspect. Run the suite with `-rf` and record the name if it fails again.
- `status`: a failed start while a manual run is live marks the live run dead; a future-dated row can show as "Last run".
- Test gaps: offline detection with `domain_down`, `finish_engine_run` failing, the schedule-warning lines in `status`.
- `sources_due` is not validated in `store.py` (protected).
- The digest's keyword tags misfire on some names (e.g. "Raghuvanshi ... long layoff" → layoffs topics, "Yash Rathod" → D14). This is open question 7; the AI pass is meant to absorb it. Tonight O02 (Siddaramaiah/Shivakumar) tagged 9 of the top 13 stories, as open question 7 predicted.
- Stories split by wording, for the AI pass: the "beyond midnight" Namma Metro title (known), and the rain coverage on 5 Oct ("Heavy rain throws Bengaluru into chaos: Waterlogging grips key roads" / "Bengaluru rain chaos: City battles waterlogging, traffic snarls..." / "Bengaluru rains trigger waterlogging, traffic snarls; Krishna Byre Gowda orders 24x7 control rooms"). They are arguably different angles, so don't force them by title.
