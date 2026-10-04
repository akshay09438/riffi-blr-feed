# Technical spec - Riffi ingestion engine

*How the engine is built. Status on 4 Oct 2026: **step 1 (fetchers) built** - every route is as-built (see "Fetchers (as built)"); everything else is the target design from `BRIEF.md` and `DECISIONS.md`. Rewrite each section to "as-built" in the same change that builds it.*

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
  config.py              settings from the environment / .env (RSSHUB_BASE_URL so far)
  sources.py             feeds.csv -> Source rows (until step 6 stores them in the database)
  db/                    [dangerous] schema, connection, idempotent CSV importers, retention
  fetchers/
    http.py              [dangerous] the one HTTP client: identity, timeouts, retries, rate limit, conditional GET
    gnews.py             [dangerous] Google News queries and link resolution (+ cache, per-run cap)
    parse.py             RSS/Atom -> one entry shape (title, link, date, summary, author, image, GN <source>)
    feeds.py             every route: plan what to fetch, fetch it, never raise (feeds and page monitors)
    pagemonitor.py       web page monitors: trafilatura text, stamp lines dropped, hash, "updated" item
    outcome.py           FetchOutcome / Validators / PageSnapshot shared by all routes
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

## Fetchers (as built, 4 Oct 2026)

- `fetchers/http.py` - `PoliteClient`, the only way the engine touches the network. Browser User-Agent (D-004); one request at a time per domain and 2-2.5 s between request starts on a domain (other domains run in parallel); 20 s read timeout plus a 60 s overall deadline; 2 retries with 2 s / 4 s backoff on network errors, 429 (Retry-After, capped at 30 s) and 5xx, none on DNS/TLS failures; redirects followed by hand (up to 10) so every hop goes through its own domain's gate; X, Instagram and WhatsApp are refused even when a redirect points there; a domain that fails 3 times in a row at network level (timeout, DNS, connect) is skipped for the rest of the run; conditional GET (ETag / Last-Modified in, 304 reported as "not modified"); 15 MB cap; TLS always verified. Never raises for a network problem - the result carries the error.
- `fetchers/parse.py` - feedparser (fed a `BytesIO`) into `Entry` (title, link, published in UTC, summary stripped of HTML and cut to 1,000 chars, author, image from media:content / media:thumbnail / enclosure / first `<img>`, Google News `<source>`, guid). Dates that carry no zone (and "... IST") are read as IST, since every source that omits the zone is Indian; feedparser alone would read them as UTC (5.5 h off) and misread `29-09-2026` as 2029. `looks_like_html` catches feeds that answer 200 with a web page.
- `fetchers/feeds.py` - `plan()` decides per route what to fetch (table in the module); `fetch_source()` returns a `FetchOutcome` (`ok` / `not_modified` / `skipped` / `error`, reason, HTTP status, entries, validators) and never raises; `fetch_sources()` runs many at once. X/Instagram rows fetch only `backup_google_news_url` and are marked `on_backup`; `rss_app_url()` is the stub for the 5 `priority_x_feed` rows. Rows whose fetch_url is an instruction (S047, S107, S121), that lack a backup (S108), or whose backup is not a Google News feed are skipped with the reason.
- `fetchers/gnews.py` - `Resolver` turns Google News article links into publisher URLs: offline base64 decode first, else the two-request online decode (article page signature, then batchexecute); if Google redirects straight to the publisher, that URL is taken. Answers are cached by article id (the cache can be passed in, so step 6 can persist it); online look-ups stop at 100 per run and the rest stay as Google News links for a later run. Wiring it into the pipeline is step 2.
- `fetchers/pagemonitor.py` - the 22 web page monitors (21 fetchable; S047 has an instruction for a URL). GET with a browser Accept header; the first 1 MB of HTML goes to trafilatura in a worker thread with a 60 s limit (trafilatura takes ~6 s at 1 MB and minutes beyond, which would otherwise freeze every other fetch). Main text with tables, or all visible text split into sentences when trafilatura finds no main text. Stamp lines are dropped: a line with a stamp word ("updated", "reviewed", "visitors", "views", "sunrise", "rights reserved", "... ago") whose other words are only filler, dates, times and numbers, plus a bare date/time line straight after one. Lines with real words, Kannada text, or only numbers/dates without a stamp word (table rows, "Date: 15/10/2026") are content. The hash is over the *set* of lines, so reordering is not a change. Bot-check pages (Cloudflare "Just a moment...", "Access denied" titles, challenge markup) are errors. First visit: snapshot only, and no conditional-GET validators are sent until a snapshot exists. Later visits: if the hash changed, one entry - title = page `<title>` + " updated" (source name if none), link = the page, published = when the change was seen, summary = "Added: ... || Removed: ..." of lines that really changed (1,000 chars max), guid = source id + old hash + new hash. Every error (HTTP, non-HTML, bot check, extraction crash or timeout) hands back the previous snapshot unchanged. Snapshots go in and out on `FetchOutcome.snapshot`; storing them (page_snapshots) is step 6.
- Not yet: storing outcomes (fetch_runs, items, page_snapshots) is step 6; `test-feeds` is step 7 (CLI).

## Normalise and clean (as built, 4 Oct 2026)

- `normalise.py` - `clean(outcomes, sources, blocklist=, resolver=, now=)` turns every entry of every successful fetch into an `Item` (BRIEF.md items shape plus `canonical_url`, `url_resolved`, `on_backup`). Order matters: cheap drops first (no link, older than 7 days, blocklisted link or Google News `<source>` domain), then Google News links are resolved (so the per-run online cap is never spent on items about to be dropped), then the resolved URL is checked against the blocklist again. A Google News link that stays unresolved is kept only if its site is known (a `<source>` URL, or a source name that is a domain) and passed the blocklist; otherwise it is held back for a later run, so a copycat cannot slip through unresolved. One odd entry (unreadable URL, missing fields, unknown source) is dropped with its reason, never stops the run. Dates missing or more than 6 h ahead become the fetch time; `published_at` / `fetched_at` are stored in IST. Publisher: Google News `<source>`, the channel name for Telegram and YouTube, otherwise the source name. Title: " - Publisher" stripped for Google News; a Telegram post with no title gets its first sentence. Language by script: `kn` when at least 30% of the letters are Kannada, `other` when another script dominates, else `en`. `canonical_url` (https, no amp/mobile variants, no tracking parameters) gives `item_id` = sha256. Every drop is counted by reason in `CleanResult.dropped`. Excluded-topic dropping needs topic tags (step 4).
- `safety/blocklist.py` - reads `blocklist.csv` generously: every domain in the `name` and `url` columns counts (rows list several, joined by " / "), a URL with a path blocks only that path (one X account, not all of X), a domain covers its subdomains; words that only look like domains ("notice.pdf", "Node.js") are ignored. Matching reads a link the way a browser does (case, trailing dot, port, user-info, backslashes, full-width dots), checks AMP copies (google.com/amp/s/..., *.cdn.ampproject.org) as the page they copy, treats twitter.com as x.com, compares paths decoded and without doubled slashes, and never raises. Not handled: link shorteners (t.co, bit.ly) are not followed. Rows with no domain in them are reported in `Blocklist.problems` for a person to fix, never guessed - today 2 rows: "Generic holiday sites (CalendarLabs, PocketHRMS, GoDigit, BankBazaar)" and "Khel Now fixture tables".

## Data model

As in `BRIEF.md` ("Data model" and step 6): sources, fetch_runs, items, story_clusters, item_topics, page_snapshots, ground_truth. Index `items` on `published_at`, `fetched_at`, `cluster_id` and the resolved URL; `fetch_runs` on `(source_id, started_at)`. Prune `raw_payload` and page text after 30 days (D-002). SQLite busy timeout of 30 s or more.

## Code reused from the panel project

Copied (never imported) from `C:\Users\Akshay\Projects\Riffi feeds` per D-006. The module-by-module map is in `docs/implementation-plan.md`.
