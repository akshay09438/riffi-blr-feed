# Laptop runbook: the news of the last 24 hours, with the Daily Picks (D-017)

*For the founder's laptop, in the project folder (today: `riffi-blr-feed-d017`). Each block is one thing to paste into a Claude Code session opened in that folder; the session runs it and reports back. Nothing here posts anything, installs the timer or deletes data. Every fetch is the founder's go-ahead (D-009).*

## A. Once, to bring the laptop up to date

1. `git checkout main-nb68vk` then `git pull origin main-nb68vk`.
2. Run the checks: `.venv\Scripts\python.exe -m pytest -q tests` (expect **586 passed, 2 xfailed**) and `-m ruff check .`.
3. Back up the database: copy `data\engine.db` with the SQLite backup method to `data\engine-backup-<today>.db`; check the table counts match.
4. `python -m riffi_ingest import-sources` (expect: 1 added, the 6 retired sources marked inactive) and `import-topics` (expect: 151 updated, 30 added).
5. `python -m riffi_ingest report`, and tell the founder the non-X **health pass rate** (target 90%; it was 89% before the retirements).

## B. Every time the founder asks for "the news of the last 24 hours"

1. Back up the database first (as in A3).
2. `python -m riffi_ingest news-24h`. It fetches every source (about 23 minutes; 35 at most), then writes **two kinds of AI batch folders** under `data\ai\`, then the digest and the Daily Picks:
   - `<time>\`, from `ai-batch`: the stories the AI has not judged yet (seven-field answers).
   - `<time>-extras\`, from `extras-batch`: the best stories of **every category** (Bangalore High first, at most 400, at least 10 per category when a category has them), **including stories the AI already judged today**, for the extra judgements. Stories older than 72 hours or excluded are never in it.
   Note the time the command took and any "failed" or "Google refused" lines. (With stories already stored and no new fetch: `news-24h --skip-fetch`, or `ai-batch` and `extras-batch` on their own.)
3. **The AI pass** (a Claude Code session; `config/prompts/ai_pass.md` is the instruction sheet). Say to the session: *"Follow config/prompts/ai_pass.md for every batch folder under data\ai that has no answers yet: write answer-NN.json for the ordinary folder and only extra-NN.json for the -extras folder, then run ai-apply on the ordinary folder and ai-facts on both."* Then:
   1. Read `topics.json` once, then every `batch-NN.json`.
   2. **Ordinary folder:** write `answer-NN.json` (seven fields per story). **Extras folder** (its batch files say `"kind": "extras"`): write only `extra-NN.json` (why the reader cares, daily-life and culture points, audience fit, routine yes/no, the two sides, a hook, a format). Editor-only subjects get no two sides; flashpoints and the other never-list subjects are `excluded: true`. **Finance and markets are framed as a debate about the system or the news, never as buy/sell/hold advice.**
   3. `python -m riffi_ingest ai-apply <ordinary folder>`. Fix any rejected answer in the same file and run it again.
   4. `python -m riffi_ingest ai-facts <extras folder>` (and `ai-facts <ordinary folder>` if you wrote extras there). Fix any rejected entry and run it again. A batch with no extra file is listed as missing; its stories are scored from signals only and marked `ai_missing`.
   5. If the plan cannot cover every batch, answer the best batches first (batch 01 holds the best stories). To send only what is still missing after a partial run, run `extras-batch` again: it skips stories that already have extras (`--again` includes them).
   6. No manual workaround is needed to give already-judged stories the new fields: that is what the extras folder is for.
4. `python -m riffi_ingest digest` then `python -m riffi_ingest picks`.
5. Read out, in plain words: the top picks by category (from `reports\<date>\picks.md`), and anything the AI excluded only because of a reason (the `ai-apply` output lists these).
6. **Still set aside by eye** any communal or religious story that appears anywhere. The safety word-list filter (proposal 4c) is not built yet.

## C. Where to look, and what good looks like

| File in `reports\<date>\` | For | Look for |
|---|---|---|
| `picks.md` | content team | 15 to 25 picks, spread over many categories, each with a reason, two sides and a content idea |
| `picks.csv` | spreadsheet | the same, one row per pick |
| `picks.json` | the seed agent | **empty until proposal 4c is built and approved. That is correct and intended** |
| `candidates.csv` | audit | every story that reached the gates and why it was not picked |
| `filter_report.md` | founder | per category: candidates, passed relevance, passed both gates, reached the pick-score floor, picked, and the top reason the others were not picked; how many stories the AI pass has not reached |
| `digest.md` / `digest.csv` | audit | all stories, now with a "Stories by category" table |
| `health.md`, `sources_health.csv` | engineering | sources failing, with their category and needs |

Signs something is wrong: fewer than 15 picks (the run says so and why), a category with many stories and no picks, picks from one topic only, a "Flags: ai_missing" on every pick after the AI pass ran (the extras did not reach `picks`: check that `ai-facts` said "stored", then run `picks` again), a pick tier of "Preview" (no AI judgement yet), a pick older than 72 hours (should not happen: tell me), two picks that are the same news (tell me the headlines), or any story about religion, caste, language or a named politician marked `pending` rather than `editor_only`.

## D. What to send back

- The time `news-24h` took, the counts it printed, and any failed sources (name them).
- The first 10 lines of `filter_report.md` and the whole `picks.md`.
- Anything in `picks.md` the founder would not want a seed account to touch.
- For the first real run only: the exact headlines of any four stories about religion or language you notice, so the safety test set can use them (proposal 4c, point 3).

## E. If something fails

- `news-24h` stops at the fetch: it printed why; run `python -m riffi_ingest status`.
- `ai-apply` or `ai-facts` rejects a file or an entry: it names the story and the reason; fix that entry in the same file and run it again (both are safe to repeat).
- `picks` says the settings cannot be read: `git pull` again, then `python -m riffi_ingest import-topics`; send me the message.
- Never edit `data\engine.db` by hand and never delete a backup.
