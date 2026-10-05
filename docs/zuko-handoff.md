# Zuko handoff - Riffi Ingestion Engine

*The single source of truth for "where things stand" between sessions. `/handoff` rewrites this at the end of a session; `/start` and the SessionStart hook replay it at the beginning. Dangerous-surface status is written as a CLAIM to re-verify, never as a settled fact.*

## Last updated

6 Oct 2026, 01:25 to 02:00 IST - laptop session (Claude Desktop, Code tab). The founder asked for "the news of the last 24 hours". Under the D-009 addendum that request is itself the go-ahead, so the session ran a backup, `fetch --all` and `digest`, and gave the top stories in plain words. Old leftovers and religious-angle headlines were set aside by hand (see "Where things stand"). No engine code, no `feeds.csv` row and no dangerous-list file changed. This handoff commit goes on the same unmerged branch (`docs/page-monitors-live-check-2026-10-06`), so one PR carries both laptop sessions. It changes only this file and `docs/implementation-plan.md`.

Before that, 5 Oct 2026 23:30 to 6 Oct 01:10 IST - laptop session (Claude Desktop, Code tab). The founder asked for: `git pull`, a backup, `import-sources`, `test-feeds` on the six blind page monitors (S004, S048, S109, S110, S008, S009) and what each now stores in `page_snapshots`. No timer. Then, each with the founder's yes: `fetch --source` on those six, saving the real S048 page as a fixture, finding new addresses for S008/S009, and switching both to Google News searches (D-015). No engine code changed. This branch (`docs/page-monitors-live-check-2026-10-06`) changes this file, `docs/implementation-plan.md`, `docs/technical-spec.md`, `DECISIONS.md` (D-015), `BRIEF.md` (route counts), the S008 and S009 rows of `feeds.csv`, the route counts in `tests/test_inputs.py` and `tests/test_fetch_feeds.py`, and adds `tests/fixtures/karnataka_gov_real_S048_kannadasiri.html`.

Before that, 5 Oct 2026, late night - cloud session: merged #34 and #33 (founder: "merge both"), then built the fix for the blind page monitors (founder: "fix the blind page monitors"; no dangerous-list file touched). Before that, 5 Oct 2026, late evening - laptop session (Claude Desktop, Code tab). The founder asked for: pull `main`, install, test, back up the database, `import-sources`, `test-feeds` on S007/S015/S041, `fetch --all`, `digest`, and the top stories of the last 24 hours. No timer. All done. Then, with the founder's yes, saved the real S004 and S109 pages as test fixtures and diagnosed the page-monitor bug offline. Then, also with the founder's yes, found a better page for DIPR press releases and re-pointed S004 to the CM's English news page (D-014). No engine code changed. This branch (`docs/handoff-2026-10-05-evening`) changes this file, `docs/implementation-plan.md`, `docs/technical-spec.md`, `DECISIONS.md` (D-014), the S004 row of `feeds.csv`, and three new files in `tests/fixtures/`.

## Where things stand

- **Merged on `main` (`855008a`):** everything up to PR #35 (the page-monitor fix), including #33 (karnatakavarthe.org blocklist row) and #34 (D-014). Before that, up to PR #32. That includes the 11 source-address fixes (#26), S007/S015/S041 on Google News (D-013, #27), dropping old cricket match pages (#28), the three small follow-ups (#29), the page-monitor pruning (#30/#31) and the grouping fix for dates and word forms (#32).
- **Checked on real data tonight:** the Google News searches for S007, S015 and S041 work. The Polish "RCB alert" headline no longer tags as RCB. Match pages are dropped (8 tonight). `status` now says "fetching is by hand for now, D-009". `test-feeds` report names now include seconds.
- **The page-monitor fix was checked live on 6 Oct (laptop): 3 of the 6 pages fixed.** S004 (CM's English page), S109 BMTC and S110 BWSSB now store their real news lists (307, 47 and 34 lines) as new baselines. **S048 is still blind:** its news is a scrolling ticker (`div.breaking-news-ticker` / `div.bn-news`) that the fix does not read; cloud item 2. **S008 ECI and S009 GBA are JavaScript apps** with nothing to read, so they moved to Google News searches (D-015). Page monitors are now 17, Google News 64.
- **The first full fetch since the D-015 switch (6 Oct, 01:27 IST) ran clean:** 130 ok, 0 failed, 1 skipped (S112, manual), 0 Google refusals, 326 new items. S008 and S009 each answered 200 with 100 items, and the validators carried over from the old sites are gone (etag and Last-Modified both empty, 0 failures). The four template page monitors answered ok with no change since their 6 Oct baselines. Non-X sources passing health: 91 of 105 (87%, target 90%), the same as on 5 Oct.
- **Religious-angle headlines reach the digest, because the exclusions filter is not built yet.** Spotted by hand in `reports/2026-10-06/digest.csv`, not counted systematically. #99 is the Karnataka Rakshana Vedike asking the government to drop an "Urdu Habba" at Vidhana Soudha. #103 is a Kannada story on a population panel asking for religion-wise data. #108 is R Ashoka accusing the Congress of "conspiring to delete Hindu votes". #185 is "Shivakumar moves to put lid on Urdu festival row". All are Medium. They were left out of what the founder was shown. Nothing reads the engine's output yet (no `/api/stories`), so nothing has reached the seed agent. This is the case for building the exclusions filter before `/api/stories`.
- **The digest's own top 30 is still rough (6 Oct, 01:42).** 8 of the 30 are leftovers stored on 5 Oct before the fixes (7 scorecard, highlight or commentary pages from S035, plus the Polish story). 10 are O02 (CM) stories, and 26 of the 30 tie at 65 points. Ranking within a score is effectively arbitrary until the AI pass adds its points.
- **Leftovers in the database (not new bugs):** the 13 old scorecards, highlight videos and the Polish story in tonight's top 30 were all stored by the two earlier runs on 5 Oct (14:14 and 18:43 IST), before the fixes landed. The same goes for the three Namma Metro copies and the three rain copies. Nothing re-cleans stored items. They leave the 24-hour window after about 18:43 IST on 6 Oct, so a digest run on the morning of 6 Oct will still show them. Removing them by hand would mean editing `data/engine.db` (dangerous), so the founder was told they will age out.
- **Not built yet:** AI tagging pass, dashboard and `/api/stories`, exclusions filter, matching the log to stories, and the recall report.
- **Founder's plan (5 Oct, late evening; D-009 addendum):** build the whole engine first. Then, whenever the founder asks for "the news of the last 24 hours", fetch and show it, and refine the output by hand over many rounds. The timer and the two-week source test come after that, so the earlier "collecting daily by 10-12 Oct" target no longer applies. **Build priority:** what makes that output good, meaning the AI pass (what's new, debate angle, better ranking), the page-monitor fix and the exclusions filter. Then the dashboard / `/api/stories`, then the recall matching.

## Do first next session

**Cloud:**
1. ~~Open and merge the two 5 Oct PRs~~ Done 5 Oct, late (cloud): #34 (handoff, D-014) and #33 (the karnatakavarthe.org blocklist row, founder's OK) merged, in that order, with CI green.
2. **Open the PR for `docs/page-monitors-live-check-2026-10-06`** (title "Live check of the page-monitor fix; S008 and S009 follow Google News (D-015)"). It changes docs, tests' route counts, one new fixture and two founder-approved `feeds.csv` rows; no dangerous-list file, so D-007 allows merging once CI is green. Then **read S048's news ticker** (`riffi_ingest/fetchers/pagemonitor.py`, not dangerous): add the ticker (`div.breaking-news-ticker` or `div.bn-news`) as a news block, tests first against `tests/fixtures/karnataka_gov_real_S048_kannadasiri.html`. Each item is a `<p>` holding an `<a>` (the title) and a hidden `<figcaption>` stamp ("2026-09-22 13:03:52"); keep the title, and decide whether the stamp line stays (it is a real date, not an age). Check that the other three saved pages read the same as before (none has the ticker). Expect S048's first fetch after the fix to re-baseline without an "updated" item.
   ~~Fix the blind page monitors~~ Built 5 Oct, late (cloud), and merged as #35. On the karnataka.gov.in template the page's text is now its news lists (`#newsModal` + `#exampleModal`, or the CM page's visible `section.news_container`), with list numbers and "N months ago" / "One year ago" ages removed; a stored snapshot that looks blind is fetched without validators, so a 304 cannot keep it. Tested on the three saved real pages. **Not yet checked live:** see laptop item 3. (DIPR's news modal has 5 entries, not 9 as written earlier.)
3. Then the build: the AI pass (open question 2 needs the founder), dashboard and `/api/stories` (dangerous), exclusions filter (dangerous; needs the founder's word lists), and matching the log to stories plus the recall report.

**Laptop (ask the founder before every fetch):**
1. Done 5 Oct: the real DIPR (old S004), BMTC (S109) and CM English (new S004) pages are saved in `tests/fixtures/` on this branch.
2. ~~Done 6 Oct: S004's new address is in the database (its etag was empty, so no carry-over).~~ Was: now that #34 has merged: `git pull`, then `import-sources` (with the founder's yes, after a backup), so the database takes S004's new address. **Until then `data/engine.db` still holds the old DIPR address.** Note the known `import-sources` bug: it keeps the old ETag when an address changes. The CM server should simply ignore an ETag it didn't issue, but check that S004's first fetch answers 200.
3. ~~Done 6 Oct (see the evidence below). `test-feeds` stores nothing, so it took `fetch --source` too.~~ Was: after the page-monitor fix merges (the new snapshots replace the blind ones with no "updated" item; S004 will store the CM page's 152 headlines): `git pull`, run the suite, back up the database, `test-feeds --source S004 --source S048 --source S109 --source S110 --source S008 --source S009`, then check `page_snapshots` holds real page text, not the policy or the JavaScript notice.
4. Give the editor `data/ground_truth.csv` and `data/ground_truth_topics.csv` before day 1 of the test.
5. ~~Done 6 Oct, 01:27 IST: both answered 200 with 100 items, and the validators cleared themselves.~~ Was: on the next fetch that includes S008 and S009 (with the founder's yes, or a "news of the last 24 hours" request), check that each answers 200. `import-sources` carried over the old sites' validators (S008 Last-Modified "Thu, 24 Sep 2026"; S009 ETag `W/"666-1791028982000"` and Last-Modified "Sat, 03 Oct 2026"). S011 had the same carry-over on 5 Oct, Google News answered 200, and the values were cleared, so this is expected to clear itself.
6. After the S048 ticker fix merges: `git pull`, suite, backup, `test-feeds --source S048`, then `fetch --source S048` with the founder's yes, and check `page_snapshots`.

## In flight

- `docs/page-monitors-live-check-2026-10-06` (laptop, 6 Oct, committed and pushed; PR to open from the cloud): D-015, the S008/S009 rows, route counts in the brief, spec and two tests, the S048 fixture, plan and this note. Plus a second commit with this 6 Oct, 02:00 handoff (docs only). Suite green (below). No dangerous-list file.
- Merged as #35: `docs/handoff-2026-10-05-evening-l4sdw8`, the page-monitor fix.
- Merged 5 Oct, late: #34 (`docs/handoff-2026-10-05-evening`) and #33 (`safety/blocklist-karnatakavarthe`, founder's OK quoted in the PR). Combined `main` re-checked in the cloud: 413 passed, 2 xfailed; ruff clean.
- Timer: **not installed and not to be offered until the whole engine is built** (D-009, confirmed by the founder 5 Oct). The install steps are in `README.md` and D-008 for when the founder changes D-009.

## How to work here

- Live runs (`test-feeds`, `fetch`, `stories`, `status`, `digest`) happen on the laptop. Cloud sessions cannot reach news sites and do not run the Zuko guard. In the cloud, check every file against the dangerous list in `CLAUDE.md` Part B yourself.
- The laptop has no `gh`, and the in-app browser is not signed in to GitHub, so open PRs from a cloud session.
- Commands: `node .claude/hooks/py.js -m ruff check .`, `-m ruff format --check .`, `-m pytest -q tests`. `py` = `.venv\Scripts\python.exe -m riffi_ingest`. On Windows, set `PYTHONIOENCODING=utf-8` when printing Kannada.
- Reading the real database for evidence: open it read-only (`sqlite3.connect('file:data/engine.db?mode=ro', uri=True)`) from a script file in the scratchpad. Inline Python through the shell loses backslashes.
- Tests can never touch `data/engine.db` (`tests/conftest.py`). `test-feeds --feeds <copy>` tests a draft copy of `feeds.csv` without touching the real one.
- The founder is non-technical: use plain language, and end confirmations with "An easy way to understand this". Ask before every fetch and before changing `feeds.csv`. The one exception: "give me the news of the last 24 hours" is itself the go-ahead. Back up the database, run `fetch --all` and `digest`, and show the top stories (D-009 addendum).

## Verification evidence (laptop, 6 Oct 2026, 01:25 to 02:00 IST)

- No `git pull`: `git fetch` showed `origin/main` had nothing that this branch lacks. The working tree was clean on `docs/page-monitors-live-check-2026-10-06` at `264de03`.
- Backup: `data/engine-backup-2026-10-06-0127.db` (SQLite backup API, 14,848,000 bytes). All 11 table counts matched: items 3,032; story_clusters 2,869; fetch_runs 399; item_topics 1,625; page_snapshots 53; gnews_cache 300; sources 131; topics 151; engine_runs 3; ground_truth 0; schema_version 1. `integrity_check` was ok on both copies.
- `fetch --all` (01:27 IST, 14.8 min, exit 0): 130 ok, 1 skipped (S112, manual), 0 failed, 0 Google refusals. Entries 9,106: 326 new, 2,417 already stored, 22 repeated within the run, 6,333 older than 7 days, 7 match-record pages, 1 blocklisted (khelnow.com). Stories: 306 new, 14 grown; 104 tagged, 42 unmatched local. Google News: 100 looked up, 100 cached. Scored 75 Medium, 29 Low, 216 Drop. `engine.log`: "2026-10-06 01:27 IST all ok sources 131, ok 130, failed 0, skipped 1, new items 326, google refusals 0, 14.8 min".
- `fetch_runs` (read-only): S008 run 407 and S009 run 408 were both `ok`, HTTP 200, 100 items. S008's newest is 5 Oct 10:22 IST and S009's is 5 Oct 22:36 IST. `sources`: S004, S008, S009, S048, S109 and S110 all have `last_status` ok, `consecutive_failures` 0, and empty etag and Last-Modified. S004, S048, S109 and S110 returned 0 items (no change), and `page_snapshots` went 53 → 70 (one row for each of the 17 page monitors).
- After the run: items 3,358; story_clusters 3,175; fetch_runs 530; item_topics 1,768; gnews_cache 400; engine_runs 4.
- `digest` (window 5 Oct 01:42 to 6 Oct 01:42 IST, so the whole database, whose first run was 5 Oct 14:14): 3,358 items, 3,175 stories (3 High, 800 Medium, 436 Low, 1,936 Drop), 1,239 in the CSV. Health: 113 working, 0 failing 3+ in a row, 17 stale, 48 not useful in 7 days; **91 of 105 non-X sources passing (87%, target 90%)**. Files are in `reports/2026-10-06/`.
- After the run, with no code changed: `pytest -q -rf tests` **418 passed, 2 xfailed** in 36.2 s; `ruff check` all passed; `ruff format --check` 65 files already formatted.

## Verification evidence (laptop, 5 Oct 2026 23:30 to 6 Oct 01:10 IST)

- `git checkout main && git pull`: fast-forwarded `80f1835` → `855008a` (#33, #34, #35). `pytest -q -rf tests` **418 passed, 2 xfailed**; `ruff check` all passed; `ruff format --check` 65 files already formatted.
- Backups (SQLite backup API; every table count matched, `integrity_check` ok on both copies): `data/engine-backup-2026-10-05-2331.db`, `-2026-10-06-0051.db` (before the six-page fetch) and `-2026-10-06-0100.db` (before the D-015 import). Earlier backups kept.
- `import-sources` (twice before the switch, once after): 0 added, 131 updated, 0 marked inactive. S004 = `https://cm.karnataka.gov.in/en`, etag empty.
- Before: all four template pages (S004 old DIPR, S048, S109, S110) stored the same privacy policy (hash `eb00a106`, 3,588 chars); S008 and S009 stored "You need to enable JavaScript to run this app." (hash `69eca094`), kept by 304s.
- `test-feeds --source S004 --source S048 --source S109 --source S110 --source S008 --source S009` (run twice, 23:32 and 00:51; reports `reports/test-feeds/2026-10-05-233254.md` and `2026-10-06-005137.md`): **4 passed, 2 failed**. `test-feeds` stores nothing; the text each page gave was captured in memory by wrapping the command's `fetch_sources` (scratchpad script, same requests). S004 307 lines (hash `5d559c07`, first "News and Events", then "No one can erase Gandhiji's name or ideology: Chief Minister D.K. Shivakumar"); S109 47 lines (`641b79f6`, "ಇತ್ತೀಚಿನ ಸುದ್ದಿಗಳು", "Student Pass", ...); S110 34 lines (`505e5ea7`, recruitment notices, "Water Shutdown on 22-07-2026"); none with policy text or an "ago" age. S048 still the policy (`eb00a106`). S008 and S009: 200 but "the page needs JavaScript".
- `fetch --source` on the six (founder's yes, 00:52 IST): 4 ok, 2 errors; 0 entries, 0 new items (items stayed 3,032), so no "updated" item. `page_snapshots` 49 → 53: new latest snapshots S004 `5d559c07`, S109 `641b79f6`, S110 `505e5ea7`, S048 `eb00a106` (unchanged text). S008/S009 kept their old snapshots; `consecutive_failures` 1 each (the earlier 304s had hidden the failure).
- S048 page saved through the engine's own client (one request, 200, 171,307 bytes) as `tests/fixtures/karnataka_gov_real_S048_kannadasiri.html`. Offline: `page_lines` gives the 21-line policy; `#newsModal`, `section.news_container` and `#exampleModal` are absent; the news is in `div#newsTicker10.breaking-news-ticker` > `div.bn-news` > `marquee` > `p` items (newest: the Rajyotsava award guidelines, 22 Sep 2026; then a culture-grant deadline extension, 20 Aug; Independence Day programmes, 12 Aug). The other saved fixtures have no such ticker.
- S008/S009 in the in-app browser: ECI's press notes come from `www.eci.gov.in/eci-backend/public/api/get-file?categories=<encrypted>&orderby=date...` (JSON; newest PN of 30 Sep 2026; the 22 Sep note on the Karnataka Graduates' and Teachers' Council elections is there). GBA calls no data API; its ticker and news are in `static/js/main.2c80bd9f.js`; `updates.bbmp.gov.in/public` is a search form with an empty table.
- Draft `feeds.csv` (only S008/S009 changed) through `test-feeds --feeds <copy>`: **2 passed**, 100 items each; S008 newest 5 Oct 10:22 IST, 11 in 7 days; S009 newest 5 Oct 22:36 IST, 8 in 7 days. Report `reports/test-feeds/2026-10-06-005644.md`. The real `feeds.csv` then took exactly those URLs (checked by diff) and reads back through `load_sources` / `plan`.
- After `import-sources` the database has Google News 64, page monitors 17, X/Instagram 25, native 19, Telegram 3, YouTube 2, manual 1.

## Verification evidence (cloud, 5 Oct 2026, late)

- `main` after merging #34 then #33 (`1075727`): `pytest -q -rf tests` **413 passed, 2 xfailed**; `ruff check` all passed. The flaky test did not show.
- Page-monitor fix, tests first: the 5 new tests ran red before the change (4 failed; the fifth, re-baselining a policy snapshot on the real pages, already passed and stays as a guard). After the change: `pytest -q -rf tests` **418 passed, 2 xfailed**; `ruff check` all passed; `ruff format --check` 65 files already formatted.
- What `page_lines` now gives on the saved pages: DIPR 6 lines (heading + 5 news items, e.g. "Land of sandalwood cinema invites global film makers"); BMTC 47 lines (news + Quick Announcements, e.g. "Student Pass"); CM English 307 lines (headlines and datelines, e.g. "No one can erase Gandhiji's name or ideology: Chief Minister D.K. Shivakumar"). None holds policy text or an "ago" age.

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

- 6 Oct, 01:25-02:00 (laptop): no dangerous-list file changed. `data/engine.db` was written only by the engine's own `fetch --all`, after the backup above. The founder's "news of the last 24 hours" request was the go-ahead (D-009 addendum). One new backup file was written into `data/` by a backup-API script. Every evidence query opened the database read-only. `digest` only reads the database.
- 6 Oct, earlier (laptop): no dangerous-list file changed. `data/engine.db` was written only by the engine's own commands (`import-sources` three times, `fetch --source` on six sources), each after a backup and with the founder's request or yes. New backup files were written into `data/` by a backup-API script. Evidence queries opened the database read-only. The in-app browser visited eci.gov.in, gba.karnataka.gov.in and updates.bbmp.gov.in only to read them. S004, S048, S109, S110, S008 and S009 answered over verified TLS.
- This session changed no dangerous-list file. `data/engine.db` was written only by the engine's own commands (`import-sources`, `fetch --all`), with the founder's yes, after the backup. Every evidence query opened it read-only.
- TLS verification is always on (truststore, D-010). S004, S009, S109 and S110 answered over verified TLS tonight.
- `config/exclusions.yaml`, `api/stories.py`, `tagging/llm*.py` and `config/prompts/**` do not exist yet.
- Still pending from the founder: the Slack channel and member ID for `ZUKO_SLACK_WEBHOOK_INGEST`. `.env.example` is not written.

## Open escalations

The karnatakavarthe.org blocklist row merged 5 Oct (#33, founder's OK). Claim to re-verify: `blocklist.csv` on `main` has exactly one `karnatakavarthe.org` row (checked in the cloud: 1). Founder decisions still open:
- Open question 2: when the AI pass runs.
- The exclusions word lists.
- `import-sources` keeps the old ETag when a source's address changes (S011 kept PIB's; harmless so far). The fix is in `db/importers.py`, which is dangerous.
- Certificates on the future server (open question 11).
- The timer stays off (D-009) until the founder says otherwise.

## Known small follow-ups (recorded by reviews, triaged "later")

- **Gaps in the blocklist filter that already exist** (found 5 Oct by the evasion review of the karnatakavarthe row; none is caused by that row). `riffi_ingest/safety/**` is dangerous, so each needs the founder:
  - a host whose dot is written `%2E` gets through;
  - proxies and redirectors get through: `<site>.translate.goog`, `google.com/url?q=`, `web.archive.org`, `t.co`, `bit.ly`;
  - an unresolved Google News link with a spoofed source URL is kept until it resolves;
  - items stored before a row is added are never re-checked.

  The last one matters only when a site turns bad after its articles were already collected. At the time of the review, 0 stored items were on any blocked site.

- **An unidentified flaky test.** After the D-014 edits (around 21:20 IST, 5 Oct), one full run gave `1 failed, 409 passed, 2 xfailed` in 40.3 s (slower than usual). The next five runs, with no change in between, were all `410 passed, 2 xfailed`. Its name was not captured, and the passing runs cleared `.pytest_cache/.../lastfailed`. The edits touched only `feeds.csv` (one row), docs and an unused fixture, and that row reads back correctly through `load_sources`. A timing-sensitive test is the likely suspect. Run the suite with `-rf` and record the name if it fails again.
- `status`: a failed start while a manual run is live marks the live run dead; a future-dated row can show as "Last run".
- Test gaps: offline detection with `domain_down`, `finish_engine_run` failing, the schedule-warning lines in `status`.
- `sources_due` is not validated in `store.py` (protected).
- The digest's keyword tags misfire on some names (e.g. "Raghuvanshi ... long layoff" → layoffs topics, "Yash Rathod" → D14). This is open question 7; the AI pass is meant to absorb it. Tonight O02 (Siddaramaiah/Shivakumar) tagged 9 of the top 13 stories, as open question 7 predicted.
- Stories split by wording, for the AI pass: the "beyond midnight" Namma Metro title (known), and the rain coverage on 5 Oct ("Heavy rain throws Bengaluru into chaos: Waterlogging grips key roads" / "Bengaluru rain chaos: City battles waterlogging, traffic snarls..." / "Bengaluru rains trigger waterlogging, traffic snarls; Krishna Byre Gowda orders 24x7 control rooms"). They are arguably different angles, so don't force them by title.
