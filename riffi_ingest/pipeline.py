"""One fetch cycle, end to end: fetch -> clean -> group into stories -> tag -> score -> store
(BRIEF.md steps 1-6).

The AI pass (step 4, part 2) is not wired in yet: stories are tagged by keyword and scored without the AI
points, marked "awaiting AI pass".
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import rsshub_base
from .db import store
from .db.connection import PROJECT_ROOT
from .dedupe import WINDOW, Clusterer
from .fetchers.feeds import fetch_sources
from .fetchers.gnews import Resolver
from .fetchers.http import PoliteClient
from .normalise import clean
from .safety.blocklist import load_blocklist
from .scoring import Scorer
from .sources import Source
from .tagging.keywords import KeywordTagger


@dataclass
class Paths:
    blocklist: Path = PROJECT_ROOT / "blocklist.csv"
    keywords: Path = PROJECT_ROOT / "config" / "topic_keywords.yaml"
    scoring: Path = PROJECT_ROOT / "config" / "scoring.yaml"


@dataclass
class RunSummary:
    sources: int = 0
    statuses: Counter = field(default_factory=Counter)  # ok / not_modified / skipped / error -> count
    entries: int = 0
    items_new: int = 0
    dropped: Counter = field(default_factory=Counter)
    stories_new: int = 0
    stories_grown: int = 0
    tagged: int = 0
    unmatched_local: int = 0
    gnews_online: int = 0
    gnews_cached_new: int = 0
    labels: Counter = field(default_factory=Counter)  # High / Medium / Low / Drop -> stories scored this run
    pruned_payloads: int = 0


async def run_fetch(
    conn: sqlite3.Connection,
    sources: list[Source],
    *,
    paths: Paths | None = None,
    client: PoliteClient | None = None,
    now: datetime | None = None,
    on_progress=None,
) -> RunSummary:
    """Fetch `sources` once and store everything new. `client` is for tests (a fake network);
    `on_progress(outcome)` is called as each source finishes.

    Order matters for crash safety: items and stories are saved before the fetch records that hold each
    source's conditional-GET validators and page snapshot. If the run dies in between, the next run simply
    re-reads those feeds (stored items are skipped); the other order could lose items for good, because the
    next run would get "not modified" for feeds whose items were never saved."""
    paths = paths or Paths()
    now = now or datetime.now(timezone.utc)
    blocklist = load_blocklist(paths.blocklist)
    tagger = KeywordTagger.load(paths.keywords)
    scorer = Scorer.load(paths.scoring)
    if conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 0:
        raise RuntimeError("no topics in the database: run `python -m riffi_ingest import-topics` first")
    by_id = {s.source_id: s for s in sources}
    summary = RunSummary(sources=len(sources))

    cache = store.load_gnews_cache(conn)
    known_cache = set(cache)
    own_client = client is None
    client = client or PoliteClient()
    try:
        outcomes = await fetch_sources(
            client,
            sources,
            rsshub_base=rsshub_base(),
            validators=store.load_validators(conn),
            snapshots=store.load_snapshots(conn),
            on_done=on_progress,
        )
        resolver = Resolver(client, cache=cache)
        cleaned = await clean(outcomes, by_id, blocklist=blocklist, resolver=resolver, now=now)
    finally:
        if own_client:
            await client.aclose()

    summary.dropped = cleaned.dropped
    summary.gnews_online = resolver.online_used

    # items already stored (in a story older than the 48 h window) are not re-clustered
    known = store.known_item_ids(conn, [i.item_id for i in cleaned.items])
    fresh = [i for i in cleaned.items if i.item_id not in known]
    clusterer = Clusterer(existing=store.load_recent_clusters(conn, now, WINDOW))
    route_types = {s.source_id: s.route_type for s in sources}
    results = clusterer.add_all(fresh, route_types)
    touched = {r.cluster.cluster_id: r.cluster for r in results if r.added}
    summary.stories_new = len({r.cluster.cluster_id for r in results if r.new_cluster})
    summary.stories_grown = len(touched) - summary.stories_new
    summary.items_new = store.save_run(conn, fresh, list(touched.values()), now)
    for outcome in outcomes:  # after the items: see the docstring
        store.record_fetch(conn, outcome, now)
        summary.statuses[outcome.status] += 1
        summary.entries += len(outcome.entries)

    tags = {cid: tagger.tag_texts(store.story_texts(conn, cid)) for cid in touched}
    store.save_tags(conn, tags, now)
    summary.tagged = sum(1 for t in tags.values() if t.topics)
    summary.unmatched_local = sum(1 for t in tags.values() if t.unmatched_local)
    for cid in touched:
        score = scorer.score(store.story_facts(conn, cid))
        store.save_score(conn, cid, score)
        summary.labels[score.label] += 1

    summary.gnews_cached_new = store.save_gnews_cache(
        conn, {k: v for k, v in resolver.cache.items() if k not in known_cache}, now
    )
    summary.pruned_payloads, _ = store.prune(conn, now)
    return summary
