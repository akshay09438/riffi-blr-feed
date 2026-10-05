"""The database schema. BRIEF.md "Data model" and step 6 name seven tables; four helpers are added:
topics (from topics.csv), gnews_cache (Google News link -> publisher URL, kept so a link is resolved
once ever, not once per run), schema_version, and engine_runs (the run diary, step 7).

Changing a table that already holds data needs a migration step here, never a drop: the two-week
test's history cannot be recreated. A new table is added with CREATE TABLE IF NOT EXISTS, which an existing
file picks up on its next connect without touching its data, so SCHEMA_VERSION stays as it is.
"""

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT,
    route_type TEXT NOT NULL,
    fetch_url TEXT,
    link_or_handle TEXT,
    topics_hint TEXT,
    tier TEXT,
    priority_x_feed INTEGER NOT NULL DEFAULT 0,
    backup_google_news_url TEXT,
    known_status TEXT,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    -- kept up to date by every fetch (BRIEF.md health checks; written back to the source sheet)
    last_status TEXT,
    last_ok_at TEXT,
    newest_item_at TEXT,
    fields_present TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    etag TEXT,
    last_modified TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    started_at TEXT NOT NULL,
    status TEXT NOT NULL,              -- ok | not_modified | skipped | error
    http_status INTEGER,
    duration_ms INTEGER,
    items_returned INTEGER NOT NULL DEFAULT 0,
    newest_item_at TEXT,
    fields_present TEXT,
    on_backup INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS fetch_runs_source_time ON fetch_runs (source_id, started_at);

CREATE TABLE IF NOT EXISTS story_clusters (
    cluster_id TEXT PRIMARY KEY,
    headline TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    started_at TEXT NOT NULL,          -- first report's publication time: the 48 h window's anchor
    source_count INTEGER NOT NULL DEFAULT 0,
    publisher_count INTEGER NOT NULL DEFAULT 0,
    sources TEXT NOT NULL DEFAULT '[]',  -- JSON list of source_ids
    page_monitor INTEGER NOT NULL DEFAULT 0,
    unmatched_local INTEGER NOT NULL DEFAULT 0,
    relevance_score REAL,
    label TEXT,                        -- High | Medium | Low | Drop (step 5)
    sensitive INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS story_clusters_started ON story_clusters (started_at);

CREATE TABLE IF NOT EXISTS items (
    item_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    publisher TEXT,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    summary TEXT,
    image_url TEXT,
    author TEXT,
    language TEXT,
    raw_guid TEXT,
    raw_payload TEXT,                  -- JSON; pruned after 30 days (D-002)
    url_resolved INTEGER NOT NULL DEFAULT 1,
    on_backup INTEGER NOT NULL DEFAULT 0,
    cluster_id TEXT REFERENCES story_clusters(cluster_id)
);
CREATE INDEX IF NOT EXISTS items_published ON items (published_at);
CREATE INDEX IF NOT EXISTS items_fetched ON items (fetched_at);
CREATE INDEX IF NOT EXISTS items_cluster ON items (cluster_id);
CREATE INDEX IF NOT EXISTS items_url ON items (canonical_url);

CREATE TABLE IF NOT EXISTS item_topics (
    cluster_id TEXT NOT NULL REFERENCES story_clusters(cluster_id),
    topic_id TEXT NOT NULL,
    match_method TEXT NOT NULL,        -- keyword | llm
    confidence REAL,
    evidence TEXT,                     -- JSON: the keywords that matched, or the AI's reason
    tagged_at TEXT NOT NULL,
    PRIMARY KEY (cluster_id, topic_id, match_method)
);
CREATE INDEX IF NOT EXISTS item_topics_topic ON item_topics (topic_id);

CREATE TABLE IF NOT EXISTS page_snapshots (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    taken_at TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    title TEXT,
    text TEXT,                         -- pruned after 30 days, except each page's latest snapshot
    PRIMARY KEY (source_id, taken_at)
);

CREATE TABLE IF NOT EXISTS ground_truth (
    gt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_on TEXT NOT NULL,           -- the date the editor logged it (IST)
    topic_id TEXT NOT NULL,
    what_happened TEXT NOT NULL,
    where_seen TEXT,
    seen_at TEXT,                      -- when the editor saw it
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topics (
    topic_id TEXT PRIMARY KEY,
    type TEXT, topic TEXT NOT NULL, date_or_window TEXT, category TEXT, geography TEXT, status TEXT,
    why_people_talk TEXT, debate_angles TEXT, spice INTEGER, noise INTEGER, priority TEXT, sensitive_note TEXT,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gnews_cache (
    article_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    resolved_at TEXT NOT NULL
);

-- The run diary (step 7, D-008): one row per fetch run and per timer check that fetched nothing, so every gap
-- in the fetch history has a known cause (laptop off or asleep, no internet, a crash). Kept forever.
CREATE TABLE IF NOT EXISTS engine_runs (
    engine_run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,          -- the run's clock: equal to its fetch_runs.started_at
    finished_at TEXT,                  -- NULL while running, or if it never finished (killed, crashed)
    mode TEXT NOT NULL,                -- due (the timer) | all | source
    outcome TEXT NOT NULL,             -- running | ok | offline | failed | nothing_due | busy
    sources_due INTEGER NOT NULL DEFAULT 0,
    sources_ok INTEGER,
    sources_failed INTEGER,
    sources_skipped INTEGER,
    items_new INTEGER,
    google_refusals INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS engine_runs_started ON engine_runs (started_at);
"""
