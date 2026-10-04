# Zuko handoff - Riffi Ingestion Engine

*The single source of truth for "where things stand" between sessions. `/handoff` rewrites this at the end of a session; `/start` and the SessionStart hook replay it at the beginning. Dangerous-surface status is written as a CLAIM to re-verify, never as a settled fact.*

## Last updated

4 Oct 2026 - bootstrap session on the founder's laptop, then moved to a Claude Code cloud session to use the founder's cloud session credits ($250, expire 5 Nov 2026).

## In flight

Nothing in flight. Bootstrap is done; no engine code exists yet.

- Branch `setup/bootstrap` holds the whole setup (one commit); `main` holds only an empty root commit so pull requests have a base. Merge `setup/bootstrap` into `main` through a PR once the founder has looked at it.
- **Still to supply (founder):** the Slack channel and member ID in the founder's second Slack account. Escalation routing in `CLAUDE.md` says "to be confirmed"; the webhook goes in the `ZUKO_SLACK_WEBHOOK_INGEST` environment variable on the laptop, never in a file. Editing `CLAUDE.md` needs the founder's OK (it is on the dangerous list).

## Do first next session

1. Read `BRIEF.md`, then `DECISIONS.md` (it wins over the brief), then `docs/implementation-plan.md` (open questions and the reuse map).
2. Start the build at step 1 of the brief's checklist (fetchers), porting from `reference/riffi-feeds/` per the reuse map - copy, never import.
3. **In a cloud session the Zuko plugin is not installed**, so the guard and `/zuko:*` commands are unavailable: check every file against the dangerous list in `CLAUDE.md` yourself, stop and ask before touching one, and open a PR per logical change. The founder reviews PRs on the laptop, where the guard runs.
4. Live `test-feeds` across all 131 sources and the two-week run happen on the laptop, not in the cloud.

## Verification evidence (which checks ran, what they returned)

All on the laptop, 4 Oct 2026, Python 3.11.9 ARM64 in `.venv`:

- `node .claude/hooks/py.js -m ruff check .` -> All checks passed.
- `node .claude/hooks/py.js -m ruff format --check .` -> 7 files already formatted.
- `node .claude/hooks/py.js -m pytest -q tests` -> 5 passed (the input files match the brief: 131 sources with the briefed route-type split, 5 priority X rows, 151 topics = 36 D + 28 O + 87 evergreen).
- Coverage run wrote `coverage/coverage.json`; the CI reshaping one-liner produced a valid per-file summary.
- `.claude/hooks/python-checks-on-stop.js` ran green.
- Zuko guard simulated on 23 paths: all 15 dangerous paths denied, all 8 ordinary paths allowed.
- `CLAUDE.md` config block and `.zuko/config.json` are identical; no unrendered template tokens.
- GitHub CI has not run yet (first push happens with the move to the cloud).

## Open escalations

None.
