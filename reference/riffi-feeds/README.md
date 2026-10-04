# Reference copy of the panel project ("Riffi Feeds")

Read-only reference material, copied on 4 Oct 2026 from `C:\Users\Akshay\Projects\Riffi feeds` (HEAD `6148941`) so a cloud session can see it. **Not part of the engine:** never import from this folder. Copy what you need into `riffi_ingest/` (with its tests), adapt it, and leave this folder as it is. Delete the folder once everything listed in `docs/implementation-plan.md` (Reuse map) has been ported.

- `feedkit/`, `fetcher/`, `tests/`, `catalog/` - the panel's current code (honest-bot rules).
- `at-0a07e29/feedkit/` - the same files as they were at commit `0a07e29`, before the founder removed browser imitation (29 Sep 2026). This engine follows the brief instead (D-004 in `DECISIONS.md`), so the browser User-Agent and the Google News `resolve()` come from here.
- The panel's tests that enforce the bot-only identity contradict D-004: do not port them.

The panel project itself stays separate and must never be changed from this repo (D-006).
