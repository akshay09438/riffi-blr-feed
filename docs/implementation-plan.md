# Implementation plan - Riffi ingestion engine

*How far along the engine is, what is in flight, what is left, and the drift log. Update it in the same change that moves any item.*

**Status on 4 Oct 2026:** steps 1-3 built (fetchers, normalise and clean, story clusters); next is step 4 (tagging). Before that: set up only. Zuko bootstrapped (Python-adapted CI and hooks), inputs and brief in the repo, a smoke test on the input files (5 passing), the panel project's reusable code copied to `reference/riffi-feeds/`. No engine code yet. Building continues in a Claude Code cloud session (founder's cloud credits, expire 5 Nov 2026); live feed tests and the two-week run stay on the laptop.

**Deadline context:** Riffi launches end of Oct / first week of Nov 2026. The two-week source test needs ~14 days of running, so the engine should be fetching by about 10-12 Oct to finish the test before launch.

## Steps (from the brief's build checklist)

| # | Step | Status |
|---|---|---|
| 0 | Look before building; choose the path | Done - standalone Python 3.11 (D-001), separate from the panel (D-006) |
| 1 | Fetchers, one per route type | Built and tested offline (4 Oct 2026): HTTP client, feed parsing, all feed routes, Google News resolution, web page monitor. Live run on the laptop with `test-feeds` (step 7) |
| 2 | Normalise and clean (incl. Google News resolution, blocklist, 7-day drop) | Built and tested offline (4 Oct 2026); excluded-topic drop waits for step 4 tags |
| 3 | De-duplicate into story clusters | Built and tested offline (4 Oct 2026); clusters live in memory until step 6 stores them |
| 4 | Tag topics (keyword pass + Claude Code AI pass, D-005) | Not started |
| 5 | Score (scoring.yaml) | Not started |
| 6 | Store (7 tables, idempotent CSV import) | Not started |
| 7 | Schedule + CLI | Not started |
| 8 | Outputs (dashboard, digest, /api/stories, sources_health.csv) | Not started |
| 9 | Health checks | Not started |
| 10 | Two-week recall test (ground-truth form, nightly match, day-14 report) | Not started |
| - | First run: test-feeds over all 131, failures with fixes, top 30 stories | Not started |
| - | README + .env.example | Not started |

## Open questions

1. **What the 07:00 digest does when the AI pass has not run** (D-005): keyword-only labels with an "awaiting AI pass" marker is the working assumption. The relevance score's "new development" (15) and "debate angle" (10) points come from the AI, so keyword-only scores run up to 25 points lower. Decide before step 5.
2. **When the Claude Code tagging pass runs:** a scheduled task in the Claude desktop app at ~06:30 IST is the working assumption, plus on demand. Decide at step 4.
3. **Rows that cannot fetch as written** (found 4 Oct 2026): S108 (X route, no backup Google News URL), S047 and S107 (fetch_url is an instruction, not a URL), S121 (YouTube channel ID needed). The first `test-feeds` run lists them; a person supplies the fix.
4. **Kannada Google News `site:` queries** (S045, S055, S056, S058) came back empty in the panel project; the publishers' own feeds worked (`prajavani.net/feed/`, `tv9kannada.com/feed`). Expect these to fail `test-feeds` and suggest the native feed.

5. **Page monitors to watch on the first live run** (found in review, 4 Oct 2026): BookMyShow (S041) and District (S042, S131) build their listings with JavaScript, so the downloaded HTML may hold little more than a shell and new events would be missed; IMD (S029) and ISL (S040) change numbers every visit (weather, scores) and may alert on most runs. Decide per source after the live run: a different URL, a JSON endpoint, changedetection.io, or drop.

6. ~~**blocklist.csv rows without a domain**~~ Resolved 4 Oct 2026: the founder supplied the domains (calendarlabs.com, pockethrms.com, godigit.com, bankbazaar.com; khelnow.com blocked as a whole site). `Blocklist.problems` still lists any future row without a domain; the health report (step 9) / `test-feeds` (step 7) must show it.

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
