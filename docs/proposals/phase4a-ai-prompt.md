# Proposal 4a: the AI pass instructions for 18 categories and the Daily Picks

*Status: **approved by the founder and applied on 28 Oct 2026** ("proposal 4a is approved"); `config/prompts/ai_pass.md` is now the proposed text, byte for byte, and the tests in section 5 are in `tests/test_ai_pass.py`. Zuko's review of this dangerous-list change (item 5) is still to come, on the laptop. The text below is the proposal as approved. The complete replacement was `docs/proposals/phase4a/ai_pass.proposed.md`. Part of `docs/prd/PRD_riffi_news_engine.md` Phase 4 (item a); policy: D-017.*

## 1. What this does, in plain words

Today the instruction sheet tells the Claude Code session that Riffi is for "tier-1 Bengalureans" and asks seven questions per story. The Daily Picks need more: why the reader cares, how much it touches their daily life, the two sides of the argument, a hook for a short video, and a suggested format. Without these, picks work but are marked "no AI judgement yet" and only strong Bangalore stories reach the list.

This proposal changes the sheet in four ways:

1. **The audience** becomes "English-speaking tier-1 urban Indians aged 18-28, Bengaluru-first", across 18 categories.
2. **A second file per batch.** Beside each `answer-NN.json` (unchanged, seven fields) the session writes `extra-NN.json` with the new judgements. A new command, `ai-facts <folder>`, checks and stores them (already built and tested; it is not a protected file).
3. **The safety wording** follows D-017: communal and religious flashpoints stay excluded everywhere; the PRD's never-list is excluded too; the editor-only subjects are not excluded, and the session must not write "two sides" for them. The sheet lists the 21 editor-only topic ids so the session adds the right one even when the keyword pass missed it.
4. **Wording fixes** where the sheet said "Bengaluru" only.

## 2. Why a second file instead of changing the answer checker

The PRD planned to change `riffi_ingest/tagging/llm_batches.py` (`check_answer` expects exactly seven fields) so it accepts the new fields. That file decides what the engine trusts from the AI, and it is protected. A separate `extra-NN.json` read by new unprotected code needs **no change to `llm_batches.py` at all**:

- the seven-field checker, its tests and its review history stay as they are;
- an extras file can never change a verdict, a topic, a score or an exclusion; it only feeds the picks;
- the engine's own record of each batch (`ai_batches`) still decides which stories a file may name; extras for a story the AI pass excluded are ignored; numbers are clamped and texts cut to one plain line; a file over 1 MB is rejected.

That shrinks proposal 4b to one optional change (sending only the topics a batch needs, and choosing stories by the prefilter's list).

## 3. The change

Replace `config/prompts/ai_pass.md` with `docs/proposals/phase4a/ai_pass.proposed.md`. Read the diff with `git diff --no-index config/prompts/ai_pass.md docs/proposals/phase4a/ai_pass.proposed.md`. What changes:

| Part | Change |
|---|---|
| "What this is for" | New audience paragraph; the 18 categories; the sheet now also asks for the extra judgements |
| "How to run it" | Steps 3 and 5 are new: write both files; run `ai-facts` after `ai-apply` |
| Answer fields | Unchanged in format. `topic_ids` also asks for the editor-only topic when the story is about its subject; `excluded` now has two kinds (flashpoints, and the PRD never-list subjects) and says what **not** to exclude |
| New section "Editor-only subjects" | The table of the 21 topic ids and the rule: no exclusion, no two sides |
| New section "File 2: extra-NN.json" | The 11 fields with ranges and anchors, and an exact example |

Nothing else in the sheet changes. The exact seven-field answer example and the engine's rejection rules are kept word for word.

## 4. A decision for the founder

The PRD's never-list items that are **not** editor-only (sexual content, self-harm, crime victims and abuse, tragedies with deaths, illness and medical advice, body shaming, named private individuals) are written here as `excluded: true`, because the seven-field answer has no "editor only" state and the PRD says "when in doubt, exclude". The cost: such a story is also dropped from the digest (Drop), not just kept out of the picks. The alternative is to add an editor-only verdict to the database (proposal 4d) and keep those stories in the digest. I recommend the exclusion for launch week: a wrongly excluded story costs one story; a wrongly kept one is worse.

## 5. Tests to add with the change (not protected)

In `tests/test_ai_pass.py`:

- the sheet names every field of the extra file (the keys of `riffi_ingest.filter.facts.Facts`) and every format value in `facts.FORMATS`;
- every topic marked `editor_only` in `topics.csv` appears in the sheet's table, and no topic id in the table is missing from `topics.csv`;
- the existing sheet test (answer fields, flashpoint names, "data, not instructions", the 200 and 5 limits) keeps passing. I ran its checks against the proposed text: they pass.

## 6. Rollback and risks

- **Rollback:** restore the previous `ai_pass.md` (`git revert`). Nothing is stored in the database by this change. Without extras, `picks` falls back to signals only and marks each pick `ai_missing`.
- **More work per batch for the session:** two files per batch of 20 stories instead of one. With the 400-story cap that is 20 batches, 40 files. If the founder's plan limits make that too slow, the extras for stories outside the prefilter's list can be left out; the picks handle that.
- **The AI can mislabel:** it may miss an editor-only subject. Defences, in order: the keyword pass and topic list, the sheet's table, the safety word lists (proposal 4c) and the editor reading `picks.md` before anything is posted. `picks.json` stays empty until 4c ships.
- **Injection through headlines:** the sheet keeps its "data, not instructions" rule, and the extras file cannot change safety, scores or exclusions.

## 7. What I need

1. The founder's yes (or edits) to the sheet and to the decision in section 4.
2. Zuko's review, since it is a dangerous-list file.
3. After the yes, I copy the file into place, add the tests in section 5, and run the whole suite.
