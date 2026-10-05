# Implementation plan - Riffi ingestion engine

*How far along the engine is, what is in flight, what is left, and the drift log. Update it in the same change that moves any item.*

**Status on 5 Oct 2026:** steps 1-6 and step 7 built (AI pass excepted): the CLI plus the scheduler (`fetch --due` every 30 minutes through Windows Task Scheduler, D-008, with a run diary and `status`). The first live run is done (see the "First run" row). The scheduler passed a safety review (three independent reviewers and an independent test author) and the founder approved its protected-file changes on 5 Oct 2026. **The timer is not installed yet:** it is registered on the laptop after this branch is merged, with the founder's separate yes. Next: install the timer, the founder's decisions A-C below, then outputs (8) and the AI pass (needs the founder on open questions 1-2). Building continues in a Claude Code cloud session (founder's cloud credits, expire 5 Nov 2026); live feed tests and the two-week run stay on the laptop.

**Deadline context:** Riffi launches end of Oct / first week of Nov 2026. The two-week source test needs ~14 days of running, so the engine should be fetching by about 10-12 Oct to finish the test before launch.

## Steps (from the brief's build checklist)

| # | Step | Status |
|---|---|---|
| 0 | Look before building; choose the path | Done - standalone Python 3.11 (D-001), separate from the panel (D-006) |
| 1 | Fetchers, one per route type | Built and tested offline (4 Oct 2026): HTTP client, feed parsing, all feed routes, Google News resolution, web page monitor. Live run on the laptop with `test-feeds` (step 7) |
| 2 | Normalise and clean (incl. Google News resolution, blocklist, 7-day drop) | Built and tested offline (4 Oct 2026); excluded-topic drop waits for step 4 tags |
| 3 | De-duplicate into story clusters | Built and tested offline (4 Oct 2026); clusters live in memory until step 6 stores them |
| 4 | Tag topics (keyword pass + Claude Code AI pass, D-005) | Keyword pass built and tested offline (4 Oct 2026), keywords drafted for all 151 topics; AI pass not started (open questions 1-2) |
| 5 | Score (scoring.yaml) | Built and tested (4 Oct 2026): every story scored and labelled each run; AI points await the AI pass (open question 1) |
| 6 | Store (7 tables, idempotent CSV import) | Built and tested (4 Oct 2026): schema, importers, run storage, pruning, wired into the CLI; the run diary (`engine_runs`) was added on 5 Oct 2026 with step 7 |
| 7 | Schedule + CLI | Built (5 Oct 2026): CLI and scheduler (`fetch --due` every 30 min through Windows Task Scheduler, D-008), run diary, `status`. The timer is written and tested but not installed yet (needs the founder's yes after merge). Digest and report come with step 8 |
| 8 | Outputs (dashboard, digest, /api/stories, sources_health.csv) | Not started |
| 9 | Health checks | Not started |
| 10 | Two-week recall test (ground-truth form, nightly match, day-14 report) | Not started |
| - | First run: test-feeds over all 131, failures with fixes, top 30 stories | Done 5 Oct 2026 on the laptop: `test-feeds` 96 passed / 35 failed; `fetch --all` 115 ok, 11 errors, 5 skipped, 2,177 new items, 2,065 stories (3 High, 610 Medium, 348 Low, 1,104 Drop). Follow-ups in `docs/zuko-handoff.md` |
| - | README + .env.example | README written (4 Oct 2026; the timer's runbook added 5 Oct); .env.example not yet (it matches the dangerous `**/.env.*` glob, so it waits for a founder OK) |

## Open questions

1. **What the 07:00 digest does when the AI pass has not run** (D-005): keyword-only labels with an "awaiting AI pass" marker is the working assumption. The relevance score's "new development" (15) and "debate angle" (10) points come from the AI, so keyword-only scores run up to 25 points lower. Decide before step 5.
2. **When the Claude Code tagging pass runs:** a scheduled task in the Claude desktop app at ~06:30 IST is the working assumption, plus on demand. Decide at step 4.
3. **Rows that cannot fetch as written** (found 4 Oct 2026): S108 (X route, no backup Google News URL), S047 and S107 (fetch_url is an instruction, not a URL), S121 (YouTube channel ID needed). The first `test-feeds` run lists them; a person supplies the fix.
4. **Kannada Google News `site:` queries** (S045, S055, S056, S058) came back empty in the panel project; the publishers' own feeds worked (`prajavani.net/feed/`, `tv9kannada.com/feed`). Expect these to fail `test-feeds` and suggest the native feed.

5. **Page monitors to watch on the first live run** (found in review, 4 Oct 2026): BookMyShow (S041) and District (S042, S131) build their listings with JavaScript, so the downloaded HTML may hold little more than a shell and new events would be missed; IMD (S029) and ISL (S040) change numbers every visit (weather, scores) and may alert on most runs. Decide per source after the live run: a different URL, a JSON endpoint, changedetection.io, or drop.

6. ~~**blocklist.csv rows without a domain**~~ Resolved 4 Oct 2026: the founder supplied the domains (calendarlabs.com, pockethrms.com, godigit.com, bankbazaar.com; khelnow.com blocked as a whole site). `Blocklist.problems` still lists any future row without a domain; the health report (step 9) / `test-feeds` (step 7) must show it.

7. **Keywords to tune after the first live run** (drafted 4 Oct 2026): the four India vs New Zealand topics share "Ind vs NZ"; several holiday topics share "long weekend"; O02 will over-tag on "Siddaramaiah"/"Shivakumar"; broad single words to watch: "boycott" (P24), "techno" (D19), "walkout" (D33), "reservation" (P15), "MoU" (D23). Kannada spellings least certain: "ಕ್ಷೇತ್ರ ಪುನರ್ವಿಂಗಡಣೆ" (SI01), "ಕೆನೆಪದರ" (P15), "ಗಿಗ್ ಕಾರ್ಮಿಕ" (P06). The AI pass is meant to absorb over-matching; missing keywords are the bigger risk.

8. **Certificates (the founder's decision A, asked 5 Oct 2026, still open):** four sources (S004, S009, S109, S110) fail because their servers send an incomplete certificate chain. Proposed fix: use the Windows trust store through the `truststore` library, which fills in the missing certificate the way a browser does; TLS stays verified. It touches `fetchers/http.py` and `requirements.txt` and is certificate configuration, so it needs the founder's explicit sign-off. The alternative is to leave them failing.

9. **Telegram (decision B, still open):** rsshub.app answers 403 for S016, S101 and S102. Options: read each channel's public page (`t.me/s/<channel>`), self-host RSSHub (the base URL is already a setting), or drop them. Until this is decided the three are expected to keep failing at every 30-minute check and to show under "failing 3+ runs" in `status`.

10. **README note on PyYAML (decision C, still open):** on the laptop (Windows ARM64, Python 3.11) PyYAML 6.0.3 has no prebuilt wheel, so the laptop session installed it without the C extension. Whether to add a note about it to the README is not decided.

## Reuse map (from the panel project, copied not imported - D-006)

| From `Riffi feeds` | Verdict | Into |
|---|---|---|
| `feedkit/net.py` (`PoliteClient`, `_Gate`, `domain_key`) | Copy and adapt: 1 req / 2 s per domain, browser UA (D-004), drop the Reddit lanes. Browser UA is in git history at commit `0a07e29` | `fetchers/http.py` |
| `feedkit/gnews.py` (+ `resolve()` from commit `0a07e29`) | Copy and adapt; keep the resolution cache and per-run cap | `fetchers/gnews.py` |
| `feedkit/parse.py` (`parse_feed`, `Entry`, `strip_html`, `parse_loose_date`) | Copy as is, add author and image | `normalise.py` / fetchers |
| `feedkit/verify.py` (`classify`, `verify_url`) | Copy and adapt | `test-feeds`, health checks |
| `fetcher/normalize.py` (`canonical_url`, `clean_title`, `norm_title`, `normalize_entry`) | Copy as is | `normalise.py` |
| `fetcher/dedupe.py` (`Clusterer.match`, ~10 lines) | Copy the logic only; switch to `token_set_ratio >= 85`; no embeddings | `dedupe.py` |
| `fetcher/topics.py` (`TopicTagger`) + Kannada lookbehind idiom | Copy and adapt | `tagging/keywords.py` |
| `fetcher/health.py`, `fetcher/daily.py` helpers | Copy patterns (3-strikes, IST day, prune) | health, digest, retention |
| `catalog/spice.yaml` `care_sensitive` list | Idea: seed for the exclusion/sensitive keyword pre-filter | `config/exclusions.yaml` |
| Tests: `tests/conftest.py` `rss()` builder; `tests/test_feedkit.py`; parts of `test_fetcher.py`, `test_signals.py` | Copy with the code they cover. **Do not copy** the tests that enforce the bot-only identity (they contradict D-004) | `tests/` |
| Local models (ONNX), translation, Reddit, the panel, post writing | Not relevant | - |

## Drift log

*When the code and a document disagree, record it here with the date and which one was fixed.*

- 4 Oct 2026 - `docs/technical-spec.md` planned one module per route (`rss.py`, `telegram.py`, `youtube.py`, `xbackup.py`). All of them are "GET a feed and parse it", so they became one `fetchers/feeds.py` (no near-copies); the page monitor stays its own module. Spec updated.
- 4 Oct 2026 - Google News resolution caps online look-ups at 100 per run (the brief gives no number; the panel project only said "cap it"). Revisit after the first live run on the laptop.
- 4 Oct 2026 - The brief's `token_set_ratio >= 85` alone merges different stories: it scores 100 whenever one title's words are inside another ("Metro fare hike" / "... rolled back"), and 86-98 for titles that differ only in a number, party, team or place. Added two rules (no conflicting detail; shorter title covers >= 70% of the longer's words) and anchored the 48 h window on a story's first report, chosen on labelled pairs in `tests/test_dedupe.py`.
- 4 Oct 2026 - The brief says page monitors ignore "date-stamp-only changes". Removing every date before comparing would also hide real changes (a new effective date), so only stamp *lines* are ignored (e.g. "Last updated: 04/10/2026 10:32 AM", visitor counters); dates inside content still count.
- 5 Oct 2026 - The brief's step 7 speeds (Google News on High-priority topics every 30 min) and APScheduler gave way to D-008: Windows Task Scheduler and `fetch --due`, Google News every 2 h during the test. The log is `engine.log` beside the database (like `fetch.lock`), not a separate logs folder, so test databases never write into the real log. Spec and plan updated.
- 5 Oct 2026 - The first design of the scheduler counted a `blocked` error as a Google refusal. In the code that kind only means a link to X, Instagram or WhatsApp was refused, so a Google refusal is a Google News feed answering 403 or 429. The design (`docs/superpowers/specs/2026-10-05-scheduler-design.md`) was rewritten to as-built after the safety review, which also added: a `failed` diary row for a check that cannot start, a strict closing of diary rows, `status` telling a running fetch from a dead one by trying the run lock, and a due rule that ignores fetch stamps dated in the future.
- 5 Oct 2026 - Before the diary table was added, the database was backed up (`data/engine-backup-2026-10-05.db`, gitignored; counts and integrity checked). `tests/conftest.py` now points every test at a temporary database, so a test that forgets `--db` can never write into `data/engine.db`.
