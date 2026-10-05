# Zuko handoff - Riffi Ingestion Engine

*The single source of truth for "where things stand" between sessions. `/handoff` rewrites this at the end of a session; `/start` and the SessionStart hook replay it at the beginning. Dangerous-surface status is written as a CLAIM to re-verify, never as a settled fact.*

## Last updated

5 Oct 2026 - cloud build session (steps 1-6 built, PRs #1-#13 merged), then the first live run on the founder's laptop through a local Claude Desktop Code session. Written by hand: the Zuko plugin is not installed in cloud sessions.

## Where things stand

- `main` is at `4a36db2` (Merge PR #13). Built and merged: fetchers for every route (#3, #6), cleaning + Google News resolution + blocklist (#7, #8), grouping into stories (#9), keyword tagging (#10), the database (#11), pipeline / health / CLI / README (#12), scoring + `stories` command (#13). CI fixes in #2 and #5; rule D-007 in #4.
- **Not built yet:** AI tagging pass, scheduler, 07:00 digest + health report, dashboard and `/api/stories`, exclusions filter, ground truth + recall test.
- **Deadline:** collecting daily by about 10-12 Oct 2026 so the two-week test finishes before launch.
- **Merge rule (D-007):** Claude may merge its own PR when every CI check is green AND no dangerous-list file changed. A PR touching a dangerous file waits for the founder's explicit OK for that change (agreeing to start the work counts if the PR does what was agreed - say so in the PR). Red check: never merge. One logical change per PR. Never commit to `main`.

## In flight

Nothing on a branch. Waiting on three founder decisions (asked 5 Oct; the founder can answer in one line, e.g. "fix certificates / public page / yes README"):

- **A. Certificates** - 4 sources (S004, S009, S109, S110) fail because their servers send an incomplete certificate chain. Proposed fix: use the Windows trust store via the `truststore` library (fills in the missing certificate the way a browser does; TLS stays verified). Touches `fetchers/http.py` and `requirements.txt` and is certificate configuration - **needs explicit sign-off**. Alternative: "skip".
- **B. Telegram** - rsshub.app answers 403 for S016, S101, S102. Options: read the public `t.me/s/<channel>` page ("public page"), self-host RSSHub ("self-host"), or "drop".
- **C. README note** - on the laptop (Windows ARM64, Python 3.11) PyYAML 6.0.3 has no prebuilt wheel; the laptop session installed it without the C extension. Add a README note ("yes README")?

## Do first next session

1. Read this file, then `BRIEF.md`, `DECISIONS.md` (wins over the brief), `docs/implementation-plan.md` (open questions 1-7, drift log) and `docs/technical-spec.md` (as-built).
2. Ask for / act on decisions A, B, C above.
3. Ranking fixes seen in the first `stories` output (one PR each):
   - Drop old-season items: ESPNcricinfo scorecards from 2013 / 2017 show as recent (their feed dates are wrong); filter on dates in the URL / title or on the source.
   - `stories --hours` filters on `updated_at`, not when the story was published. Fix in `top_stories` - **`riffi_ingest/db/store.py` is dangerous**: get the founder's OK first.
   - Count within-run duplicates in `RunSummary` (226 entries were not accounted for in the first run's summary).
   - Tighten the "RCB" keyword (matched a Polish security alert).
   - The Namma Metro timing story showed up as 3 separate stories: look at why grouping missed them (check against the labelled SAME / DIFFERENT pairs in `tests/test_dedupe.py`).
4. Give the founder a message for the laptop session to fix and re-test `feeds.csv` URLs: S057 (VK, 404), S007 (KSEC, DNS), S055 / S045 / S058 (native Kannada feeds), S073 (BOOM), S021 (PRS), S011 (PIB). The founder must supply S047, S107, S108, S121.
5. Next build steps: 07:00 digest + health report (step 8), scheduler (step 7; Windows Task Scheduler vs APScheduler still undecided), dashboard + `/api/stories` (dangerous), AI pass (open questions 1 and 2: suggested digest is keyword-only marked "awaiting AI" when the pass has not run; AI run at 06:30), exclusions filter (dangerous; needs the founder's word lists), ground truth + recall test.

## How to work here

- **Cloud sessions cannot reach news sites** (network policy "Default - trusted network access" gives 403; the founder's switch to Full did not take effect). Build and unit-test in the cloud; live runs (`test-feeds`, `fetch`, `stories`) happen on the laptop through a local Claude Desktop Code session. Remote Control and Cowork both need the laptop switched on.
- In the cloud the Zuko guard does not run: check every file against the dangerous list in `CLAUDE.md` Part B before changing it.
- Cloud quirk: PyYAML needs `pip install --ignore-installed pyyaml==6.0.3`.
- Commands: `node .claude/hooks/py.js -m ruff check .`, `-m ruff format --check .`, `-m pytest -q tests`.
- The founder is non-technical: plain language, and end confirmations with "An easy way to understand this".

## Verification evidence (which checks ran, what they returned)

- Cloud, 5 Oct 2026, on `main` at `4a36db2`: ruff check and format clean; `pytest -q tests` -> 181 passed. CI on PR #13: all five checks green (Verify, gitleaks, semgrep, coverage no-regression, Goodnight merge-gate).
- Laptop, 5 Oct 2026 (first live run, reported by the laptop session, not re-run here):
  - `test-feeds`: 96 passed, 35 failed.
  - `fetch --all`: 115 ok, 11 errors, 5 skipped; 8,154 entries read; 2,177 new items; 5,751 dropped as older than 7 days; 2,065 stories; 957 tagged; 355 with an unmatched local word; 100 Google News online look-ups (the cap).
  - Labels: 3 High, 610 Medium, 348 Low, 1,104 Drop (AI points still 0, so scores are low by design).
- Failures by group: gone quiet 17 (S003, S021, S037, S043, S054, S055, S059, S062, S073, S077, S080, S083, S085, S095, S099, S100, S114); blocked 5 (S015, S041 403; S016, S101, S102 rsshub.app 403); incomplete certificate chain 4 (S004, S009, S109, S110); missing input 4 (S047, S107, S108, S121); empty 3 (S011, S045, S058); dead 2 (S007 DNS, S057 404).

## Dangerous-surface status (claims to re-verify)

- `fetchers/http.py`, `fetchers/gnews.py`, `safety/blocklist.py`, `blocklist.csv`, `db/**`, `requirements.txt`, `.github/workflows/ci.yml` and the hook nosemgrep notes were changed with the founder's explicit OK in this session (see each PR). TLS verification is always on.
- `config/exclusions.yaml`, `api/stories.py`, `tagging/llm*.py`, `config/prompts/**` do not exist yet.
- Still pending from the founder: the Slack channel and member ID for `ZUKO_SLACK_WEBHOOK_INGEST` (an environment variable, never a file); `.env.example` not written (matches a dangerous glob).

## Open escalations

None beyond decisions A, B, C above.
