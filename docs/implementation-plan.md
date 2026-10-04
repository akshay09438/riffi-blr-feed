# Implementation plan - Riffi ingestion engine

*How far along the engine is, what is in flight, what is left, and the drift log. Update it in the same change that moves any item.*

**Status on 4 Oct 2026:** set up only. Zuko bootstrapped (Python-adapted CI and hooks), inputs and brief in the repo, a smoke test on the input files (5 passing), the panel project's reusable code copied to `reference/riffi-feeds/`. No engine code yet. Building continues in a Claude Code cloud session (founder's cloud credits, expire 5 Nov 2026); live feed tests and the two-week run stay on the laptop.

**Deadline context:** Riffi launches end of Oct / first week of Nov 2026. The two-week source test needs ~14 days of running, so the engine should be fetching by about 10-12 Oct to finish the test before launch.

## Steps (from the brief's build checklist)

| # | Step | Status |
|---|---|---|
| 0 | Look before building; choose the path | Done - standalone Python 3.11 (D-001), separate from the panel (D-006) |
| 1 | Fetchers, one per route type | Not started |
| 2 | Normalise and clean (incl. Google News resolution, blocklist, 7-day drop) | Not started |
| 3 | De-duplicate into story clusters | Not started |
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

- (none yet)
