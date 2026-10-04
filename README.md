# Riffi news ingestion engine

Collects new developments on Riffi's 151 tracked topics from 131 sources, cleans them, groups them into stories and tags them by topic. It only collects, ranks and reports: it never posts anything. What it does and why: `BRIEF.md`; where the team decided differently: `DECISIONS.md`; how it is built: `docs/technical-spec.md`; how far along it is: `docs/implementation-plan.md`.

**Built so far:** fetching every route, cleaning, Google News link resolution, the blocklist, grouping into stories, keyword tagging, scoring and labels, the database, and the commands below. **Not yet:** the AI tagging pass, the scheduler, the 07:00 digest, the dashboard and `/api/stories`, and the two-week recall test.

## Setup (Windows laptop, once)

In PowerShell, inside this folder (`C:\Users\Akshay\Projects\Riffi ingestion engine`, which is outside OneDrive on purpose):

```powershell
py -V:3.11-arm64 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then every command below is run as `.venv\Scripts\python.exe -m riffi_ingest <command>`.

## Commands

| Command | What it does |
|---|---|
| `test-feeds` | Fetches every source once and prints a pass / fail table (HTTP status, items, newest item, fields) with a suggested fix for each failure. Also writes it to `reports/test-feeds/<date-time>.csv` and `.md`. Stores nothing. Add `--source S004` (repeatable) to test a few. Takes about 3-4 minutes for all 131: each site gets at most one request every 2 seconds, and 84 sources are on Google News. A progress line shows each source as it finishes. |
| `import-sources` | Loads `feeds.csv` into the database. Safe to run again after editing the CSV: rows are updated, history is kept, and a removed source is marked inactive, never deleted. |
| `import-topics` | Loads `topics.csv` into the database. Safe to run again. |
| `fetch --all` | One full cycle for every active source: fetch, clean, group into stories, tag, store. `fetch --source S004 --source S011` does just those. The first `fetch` imports the sources by itself. Takes up to about 12 minutes (the source fetches plus up to 100 Google News link look-ups); please leave it running. Only one fetch can run at a time. |
| `stories` | The best stories of the last 24 hours, highest score first: score, label (High / Medium / Low / Drop), number of sources, topics and headline. `--top 50`, `--hours 48`, `--label High`. Until the AI pass exists, scores leave out its 25 points and stories show `[awaiting AI]`. |

Every command has `--help`.

## The first run (BRIEF.md "FIRST RUN")

1. `test-feeds` and read the failures and their suggested fixes. Known ones before any run: S047 and S107 have an instruction where the URL should be, S108 has no backup Google News URL, S121 needs a YouTube channel ID.
2. Fix what you can in `feeds.csv`, then `import-sources`.
3. `fetch --all`, then `stories` to see the top 30 with their scores and topics (a dashboard comes in step 8).

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `RIFFI_DB_PATH` | `<this folder>\data\engine.db` | The database file. Never put it inside OneDrive (D-001): the engine refuses to. |
| `RSSHUB_BASE_URL` | `https://rsshub.app` | Where Telegram channels are read from. Point it at a self-hosted RSSHub if rsshub.app blocks or rate-limits. |
| `ZUKO_SLACK_WEBHOOK_INGEST` | (none) | For the Zuko escalation hook; a secret, set on the machine, never written in a file. |

No API keys are needed: AI tagging will run in Claude Code on the founder's plan (D-005).

## Files the team edits

| File | What it is | Notes |
|---|---|---|
| `feeds.csv` | The 131 sources (an export of Source List v4) | After editing, run `import-sources`. |
| `topics.csv` | The 151 topics | After editing, run `import-topics`, and give a new topic keywords (below). |
| `config/scoring.yaml` | The relevance-score weights and label cut-offs | Tune after the two-week test. |
| `config/topic_keywords.yaml` | 5-15 keywords per topic for the keyword pass | Plain phrases; the rules are at the top of the file. A test fails if a topic in `topics.csv` has no keywords. |
| `blocklist.csv` | Copycat and unreliable sites; anything from them is dropped | Protected file: changes need the founder. Put the site's domain in the row; a row without one is reported, never guessed. |

### Adding a source

Add a row to `feeds.csv` with a new `source_id` and one of the route types: `Native publisher RSS/Atom`, `Google News RSS`, `X/Instagram via RSS.app` (fill `backup_google_news_url`; X and Instagram are never fetched directly), `RSSHub Telegram`, `YouTube Atom` (a `https://www.youtube.com/feeds/videos.xml?channel_id=...` URL), `Web page monitor`, or `Manual`. Then `test-feeds --source <id>` and `import-sources`.

### Adding a topic

Add a row to `topics.csv`, then an entry with the same `topic_id` in `config/topic_keywords.yaml` (label, 5-15 keywords, `local: true` if the keywords are generic and the topic is about Bengaluru / Karnataka), then `import-topics`.

## For developers

- Tests: `.venv\Scripts\python.exe -m pytest -q tests`; lint: `-m ruff check .` and `-m ruff format --check .` (`node .claude/hooks/py.js ...` picks the right Python on the laptop or in the cloud).
- Rules for AI agents working here, including the list of protected files: `CLAUDE.md`.
