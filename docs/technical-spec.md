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
  cli.py                 (as built) import-sources, import-topics, test-feeds, fetch; later digest, report
  pipeline.py            (as built) one fetch cycle: fetch -> clean -> stories -> tags -> store
  health.py              (as built) health checks and suggested fixes
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
  scoring.py             (as built) relevance score and label from config/scoring.yaml
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

- `normalise.py` - `clean(outcomes, sources, blocklist=, resolver=, now=)` turns every entry of every successful fetch into an `Item` (BRIEF.md items shape plus `canonical_url`, `url_resolved`, `on_backup`). Order matters: cheap drops first (no link, older than 7 days, blocklisted link or Google News `<source>` domain), then Google News links are resolved (so the per-run online cap is never spent on items about to be dropped), then the resolved URL is checked against the blocklist again. A Google News link that stays unresolved is kept only if its site is known (a `<source>` URL, or a source name that is a domain) and passed the blocklist; otherwise it is held back for a later run, so a copycat cannot slip through unresolved. One odd entry (unreadable URL, missing fields, unknown source) is dropped with its reason, never stops the run. Dates missing or more than 6 h ahead become the fetch time; `published_at` / `fetched_at` are stored in IST. Publisher: Google News `<source>`, the channel name for Telegram and YouTube, otherwise the source name. Title: " - Publisher" stripped for Google News; a Telegram post with no title gets its first sentence. Language by script: `kn` when at least 30% of the letters are Kannada, `other` when another script dominates, else `en`. `canonical_url` (https, no amp/mobile variants, no tracking parameters) gives `item_id` = sha256; Google News items are keyed on Google's article id instead, so a link stored unresolved and resolved later stays one item. Every drop is counted by reason in `CleanResult.dropped`. Excluded-topic dropping needs topic tags (step 4).
- `safety/blocklist.py` - reads `blocklist.csv` generously: every domain in the `name` and `url` columns counts (rows list several, joined by " / "), a URL with a path blocks only that path (one X account, not all of X), a domain covers its subdomains; words that only look like domains ("notice.pdf", "Node.js") are ignored. Matching reads a link the way a browser does (case, trailing dot, port, user-info, backslashes, full-width dots), checks AMP copies (google.com/amp/s/..., *.cdn.ampproject.org) as the page they copy, treats twitter.com as x.com, compares paths decoded and without doubled slashes, and never raises. Not handled: link shorteners (t.co, bit.ly) are not followed. Rows with no domain in them are reported in `Blocklist.problems` for a person to fix, never guessed (none today; the two such rows got their domains on 4 Oct 2026).

## De-duplicate into story clusters (as built, 4 Oct 2026)

- `dedupe.py` - `Clusterer` groups items into stories. Same `item_id` = same item, stored once. Otherwise an item joins the story whose title matches best, if it was published within 48 h of that story's *first* report (so recurring items like "Gold rate today" start a new story instead of chaining forever). A match needs all three: rapidfuzz `token_set_ratio` >= 85 on normalised titles (BRIEF.md); no conflicting detail (two different sets of numbers, or a word on each side with no near-match on the other, e.g. BJP/Congress, Delhi/Bengaluru, today/tomorrow - "floats"/"floated" are near-matches at `ratio` >= 75); and the shorter title has >= 70% as many meaningful words as the longer (so "Metro fare hike" cannot swallow "Metro fare hike rolled back"). `token_set_ratio` alone scores 100 whenever one title's words sit inside another, which the last two rules stop. The rules were chosen on labelled Bengaluru headline pairs kept in `tests/test_dedupe.py` (all same-story pairs merge, no different-story pair does) - add pairs there when a live run shows a mistake. A trailing " - Publisher" / " | Publisher" is ignored when comparing. Page-monitor items never join stories by title. A story keeps every member; `headline` = the earliest report, `first_seen_at`, `started_at`, `sources`, `source_count` (distinct sources, drives scoring) and `publisher_count`. Earlier stories (step 6: the last 48 h from the database) are passed in as `existing`. Speed: candidates come from one rapidfuzz bulk scan, each distinct title is stored once per story; 200 new items against 16,000 stored take about 5 s, 5,000 fresh items about 1.5 s.
- `normalise.py` - page-monitor items get `item_id` from the page URL plus the change's guid, since every change of one page has the same URL. `norm_title` keeps combining marks, so Kannada vowel signs no longer split words into fragments.

## Tagging, keyword pass (as built, 4 Oct 2026)

- `config/topic_keywords.yaml` - team-editable: for each of the 151 topics a label and 5-15 plain keyword phrases (1,904 in all, drafted by Claude from each topic's title and debate angles on 4 Oct 2026), optional `local: true` (64 topics) and optional `exclude` phrases; plus the `local_context` list of place and body names. The header explains the rules for editors.
- `tagging/keywords.py` - `KeywordTagger.load(path)`; `tag(text)` / `tag_texts([...])` return `TagResult(topics={topic_id: [matched keywords]}, local, unmatched_local)`. Matching is case-insensitive; English keywords match whole words only; Kannada keywords match at a word start (suffixes allowed; `\b` does not work in Kannada script); keywords are plain text, never regexes; the longest keyword at a position is reported. A story is tagged from all its members' titles and summaries at once. `local: true` topics need a `local_context` name somewhere in the story; `exclude` vetoes. This pass favours recall - the AI pass (step 4, part 2) confirms or rejects candidates. `unmatched_local` = names Bengaluru/Karnataka but matches no topic (BRIEF.md: weekly human review). `check_against_topics_csv` lists topics.csv ids without keywords and vice versa (a test keeps it empty).

## Pipeline and commands (as built, 4 Oct 2026)

- `fetchers/feeds.fetch_sources` runs different sites in parallel but each site's sources one after another, and Google News look-ups run one at a time: the client counts a request's 60 s limit from when it starts waiting its turn, so queueing all 84 Google News feeds at once would time most of them out unsent (found in review; a test reproduces it).
- `pipeline.run_fetch(conn, sources)` - one cycle: load conditional-GET validators, page snapshots and the Google News cache from the database; fetch; clean (incl. Google News resolution); drop items already stored; group into stories against the last 48 h of stories; save items and stories in one transaction; then record every fetch's health, validators and page snapshots (after the items, so a crash between the two only means re-reading those feeds next time, never losing items); tag each touched story from all its stored reports; save new Google News resolutions; prune (D-002). Returns a `RunSummary` (statuses, entries, new items, drops by reason, new / grown stories, tagged, unmatched local, Google News look-ups).
- `health.check(outcome, now)` - BRIEF.md checks Reachable / Valid / Alive (7 days) / Fields (title, link, date) with a suggested fix per failure (wrong URL, blocks bots, rsshub.app refusing vs channel not found, needs a channel ID or backup URL, page-not-feed, Google News query empty, a monitored page with no readable text). "Useful" needs history and belongs to the daily report.
- `cli.py` (Typer; `python -m riffi_ingest`): `import-sources`, `import-topics`, `test-feeds` (stores nothing; prints the table and writes `reports/test-feeds/<IST stamp>.csv/.md`), `fetch --all | --source ID` (imports sources on the first run; one fetch at a time via an OS file lock beside the database; a progress line per source and a time estimate; clear errors for unknown ids, `--all` with `--source`, or a missing feeds.csv; console output never crashes on characters the console cannot show). Default paths are anchored on the project folder. `digest`, `report` and the scheduler come with steps 7-8.

## Scoring (as built, 4 Oct 2026)

- `config/scoring.yaml` - team-editable weights: the BRIEF.md table exactly (priority 30/20/10, geography 25/20/12/10, new development 15, debate angle 10, sources 3+/2/1 = 10/5/0, tier Official/Media/Aggregator = 10/6/3) and labels (High 70+, Medium 45+, Low 20+, else Drop). Mapping of the CSVs' richer values: geography = the first place named ("Pan-India (Bangalore angle)" = Pan-India; Bangalore = Bengaluru; Tamil Nadu = South India); tier = the word before any bracket ("Media (Kannada)" = Media), with Fact-checker / Research / Civic org / Union scored as Media (6). A test checks every real geography and tier value maps to points.
- `scoring.py` - `Scorer.score(StoryFacts)` -> total, label, sensitive, awaiting_ai, per-factor parts, best topic. A story scores by its best topic (priority + geography); the best source tier among its sources counts. Excluded -> Drop regardless (step 4 part 2); a topic with a `sensitive_note` sets the sensitive flag but keeps the score. Without the AI pass the AI points are 0 and `awaiting_ai` is true (open question 1): the highest score possible is then 75, so High needs a High-priority Bengaluru topic with 2+ sources and an Official source, or 3+ sources.
- `db/store.py` - `story_facts` (topics: the AI's when present, else keyword; tiers of the story's distinct sources), `save_score` (relevance_score, label, sensitive), `top_stories`.
- Run: every story touched by a run is re-scored after tagging. CLI: `stories [--top 30] [--hours 24] [--label High]` lists the best stories (score, label, sources, topics, headline, [sensitive], [awaiting AI]) - the brief's first-run "top 30". The first `fetch` imports topics.csv too; a run without topics stops with a clear message (every story would otherwise score Drop).

## Data model (as built, 4 Oct 2026)

One SQLite file, `<project>/data/engine.db` (or `RIFFI_DB_PATH`), anchored on the project folder rather than the current folder (Windows Task Scheduler starts in System32), refused inside OneDrive (D-001); WAL, foreign keys on, 30 s busy timeout. Every date is ISO-8601 in UTC, so text order is time order. Tables (`riffi_ingest/db/schema.py`):

- **sources** - feeds.csv columns + `active` + health columns kept by every fetch: `last_status`, `last_ok_at`, `newest_item_at`, `fields_present`, `consecutive_failures`, and the conditional-GET `etag` / `last_modified`.
- **fetch_runs** - one row per source per run: status (ok / not_modified / skipped / error), `http_status`, `duration_ms`, `items_returned`, `newest_item_at`, `fields_present`, `on_backup`, `error`. Indexed on (source_id, started_at).
- **items** - the BRIEF.md items shape plus `canonical_url`, `url_resolved`, `on_backup`, `cluster_id`. `raw_payload` (JSON) is cleared after 30 days (D-002). Indexed on published_at, fetched_at, cluster_id, canonical_url.
- **story_clusters** - `headline`, `first_seen_at`, `started_at` (first report; the 48 h window's anchor), `source_count`, `publisher_count`, `sources` (JSON), `page_monitor`, `unmatched_local`, and `relevance_score` / `label` / `sensitive` for step 5.
- **item_topics** - (cluster_id, topic_id, match_method keyword|llm), `confidence`, `evidence` (matched keywords as JSON, or the AI's reason). Keyword rows are recomputed from the whole story each run; llm rows are never touched by the keyword pass.
- **page_snapshots** - every page-monitor snapshot; `text` cleared after 30 days except each page's latest (the next comparison needs it).
- **ground_truth** - the editor's log: `logged_on`, `topic_id`, `what_happened`, `where_seen`, `seen_at` (step 10).
- Helpers: **topics** (topics.csv), **gnews_cache** (Google News article id -> publisher URL, so a link is resolved once ever), **schema_version** (one row; a newer file is refused, never overwritten).

`db/importers.py` seeds sources and topics; re-importing updates rows in place, keeps health columns and history, and marks a source that left feeds.csv `active = 0` (never deleted). `db/store.py` records each fetch, loads validators / page snapshots / the Google News cache / the last 48 h of stories, saves a run's items and stories in one transaction (an item already stored keeps its story; a story is written only when it gains a new item; counts and sources are recomputed from stored items, so an old article listed again never shrinks or duplicates a story), saves keyword tags, and prunes. Nothing deletes rows; SQL statements are fixed text with `?` values only.

## Code reused from the panel project

Copied (never imported) from `C:\Users\Akshay\Projects\Riffi feeds` per D-006. The module-by-module map is in `docs/implementation-plan.md`.
