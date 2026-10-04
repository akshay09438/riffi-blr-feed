# Technical spec - Riffi ingestion engine

*How the engine is built. Status on 4 Oct 2026: **planned, nothing built yet** - this is the target design from `BRIEF.md` and `DECISIONS.md`. Rewrite each section to "as-built" in the same change that builds it.*

## Stack

- Python 3.11 (ARM64 on the founder's laptop: `py -V:3.11-arm64`), one virtual environment in `.venv/`.
- SQLite for phase 1 (`data/engine.db`), written so it can move to Postgres later.
- Fetching: httpx. Parsing: feedparser (RSS/Atom, RSSHub Telegram, YouTube Atom). Page text: trafilatura.
- De-duplication: RapidFuzz (`token_set_ratio >= 85` within 48 h).
- Scheduling: APScheduler. CLI: Typer. Dashboard and `/api/stories`: FastAPI, bound to `127.0.0.1` (D-003).
- AI tagging: a Claude Code session on the founder's plan, through files (D-005). No Anthropic API key.
- Tooling: pytest (+ pytest-cov), ruff (lint + format). No type checker.

## Planned layout

The dangerous-path globs in `CLAUDE.md` point at these exact names. Build them with these names, or update the danger list in the same change.

```
riffi_ingest/
  __init__.py
  __main__.py            python -m riffi_ingest -> the Typer CLI
  cli.py                 import-sources, import-topics, test-feeds, fetch, digest, report
  config.py              settings from the environment / .env
  db/                    [dangerous] schema, connection, idempotent CSV importers, retention
  fetchers/
    http.py              [dangerous] the one HTTP client: identity, timeouts, retries, rate limit, conditional GET
    gnews.py             [dangerous] Google News queries and link resolution (+ cache, per-run cap)
    rss.py, pagemonitor.py, telegram.py, youtube.py, xbackup.py
  normalise.py           one item shape, HTML stripping, IST dates, language detection
  dedupe.py              URL de-dup and title clustering
  safety/                [dangerous] blocklist, excluded-topic drop, sensitive flag
  tagging/
    keywords.py          keyword pass (reads config/topic_keywords.yaml)
    llm_batches.py       [dangerous] writes batches for the Claude Code pass, reads and validates answers
  scoring.py             relevance score from config/scoring.yaml
  scheduler.py           APScheduler jobs (30 min / 2 h / 6 h / 07:00 IST)
  outputs/
    digest.py            reports/YYYY-MM-DD/ Markdown + CSV, sources_health.csv
    sheets.py            [dangerous] (later) writing back to the team's Google Sheet
  api/
    app.py               dashboard pages
    stories.py           [dangerous] GET /api/stories?since=&label= (the seed-account agent's contract)
config/
  scoring.yaml           weights (team-tunable)
  topic_keywords.yaml    keywords and aliases per topic (team-tunable)
  exclusions.yaml        [dangerous] excluded communal/religious topics and their keywords
  prompts/               [dangerous] the instructions the Claude Code tagging pass follows
feeds.csv, topics.csv    inputs (team-editable)
blocklist.csv            [dangerous] copycat / unreliable domains
data/                    [dangerous, gitignored] engine.db, page snapshots, ground truth
reports/                 [gitignored] daily outputs
tests/
```

## Data model

As in `BRIEF.md` ("Data model" and step 6): sources, fetch_runs, items, story_clusters, item_topics, page_snapshots, ground_truth. Index `items` on `published_at`, `fetched_at`, `cluster_id` and the resolved URL; `fetch_runs` on `(source_id, started_at)`. Prune `raw_payload` and page text after 30 days (D-002). SQLite busy timeout of 30 s or more.

## Code reused from the panel project

Copied (never imported) from `C:\Users\Akshay\Projects\Riffi feeds` per D-006. The module-by-module map is in `docs/implementation-plan.md`.
