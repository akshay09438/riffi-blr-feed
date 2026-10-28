# Laptop runbook: the news of the last 24 hours, with the Daily Picks (D-017)

*For the founder's laptop, in the project folder (today: `riffi-blr-feed-d017`). Each block is one thing to paste into a Claude Code session opened in that folder; the session runs it and reports back. Nothing here posts anything, installs the timer or deletes data. Every fetch is the founder's go-ahead (D-009).*

## A. Once, to bring the laptop up to date

1. `git checkout main-nb68vk` then `git pull origin main-nb68vk`.
2. Run the checks: `.venv\\Scripts\\python.exe -m pytest -q tests` (expect **528 passed, 2 xfailed**) and `-m ruff check .`.
3. Back up the database: copy `data\\engine.db` with the SQLite backup method to `data\\engine-backup-<today>.db`; check the table counts match.
4. `python -m riffi_ingest import-sources` (expect: 1 added, the 6 retired sources marked inactive) and `import-topics` (expect: 151 updated, 30 added).
5. `python -m riffi_ingest report`, and tell the founder the non-X **health pass rate** (target 90%; it was 89% before the retirements).

## B. Every time the founder asks for "the news of the last 24 hours"

1. Back up the database first (as in A3).
2. `python -m riffi_ingest news-24h`. It fetches every source (about 23 minutes; target 35 at most), writes the AI batches, then the digest and the Daily Picks. Note the time it took and any "failed" or "Google refused" lines.
3. **The AI pass** (a Claude Code session answers the batches):
   - Open the folder the command printed (`data\\ai\\<time>\\`). Read `topics.json`, then for each `batch-NN.json` write `answer-NN.json` by following `config/prompts/ai_pass.md`.
   - **Once proposal 4a is applied** (the sheet then says to write two files), also write `extra-NN.json` beside each answer file.
   - `python -m riffi_ingest ai-apply <folder>`. Fix and repeat any rejected answer.
   - **Once 4a is applied:** `python -m riffi_ingest ai-facts <folder>`. Fix and repeat anything it rejects.
4. `python -m riffi_ingest digest` then `python -m riffi_ingest picks`.
5. Read out, in plain words: the top picks by category (from `reports\\<date>\\picks.md`), and anything the AI excluded only because of a reason (the `ai-apply` output lists these).
6. **Still set aside by eye** any communal or religious story that appears anywhere. The safety word-list filter (proposal 4c) is not built yet.

## C. Where to look, and what good looks like

| File in `reports\\<date>\\` | For | Look for |
|---|---|---|
| `picks.md` | content team | 15 to 25 picks, spread over many categories, each with a reason, two sides and a content idea (the last three only after 4a) |
| `picks.csv` | spreadsheet | the same, one row per pick |
| `picks.json` | the seed agent | **empty until proposal 4c is built and approved. That is correct and intended** |
| `candidates.csv` | audit | every story that reached the gates and why it was not picked |
| `filter_report.md` | founder | how many stories passed each gate per category, why others fell out, and how many stories the AI pass has not reached |
| `digest.md` / `digest.csv` | audit | all stories, now with a "Stories by category" table |
| `health.md`, `sources_health.csv` | engineering | sources failing, with their category and needs |

Signs something is wrong: fewer than 15 picks (the run says so and why), a category with many stories and no picks, picks from one topic only, a "Flags: ai_missing" on every pick after the AI pass ran (the extras did not reach `picks`: rerun `ai-facts`, then `picks`), or any story about religion, caste, language or a named politician marked `pending` rather than `editor_only`.

## D. What to send back

- The time `news-24h` took, the counts it printed, and any failed sources (name them).
- The first 10 lines of `filter_report.md` and the whole `picks.md`.
- Anything in `picks.md` the founder would not want a seed account to touch.
- For the first real run only: the exact headlines of any four stories about religion or language you notice, so the safety test set can use them (proposal 4c, point 3).

## E. If something fails

- `news-24h` stops at the fetch: it printed why; run `python -m riffi_ingest status`.
- `ai-apply` or `ai-facts` rejects a file: it names the story and the reason; fix that entry in the same file and run it again (both are safe to repeat).
- `picks` says the settings cannot be read: `git pull` again, then `python -m riffi_ingest import-topics`; send me the message.
- Never edit `data\\engine.db` by hand and never delete a backup.
