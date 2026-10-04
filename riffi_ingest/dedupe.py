"""Step 3 (BRIEF.md "STEP 3: DE-DUPLICATE INTO STORY CLUSTERS"): group items into stories.

1. Same item id (same canonical URL) = the same item, stored once.
2. A title similar enough to a title already in a story, published within 48 hours of it, joins that
   story: rapidfuzz token_set_ratio >= 85 on the normalised titles (BRIEF.md). token_set_ratio scores
   100 whenever one title's words are all inside the other, so a short title would swallow every story
   it is a word of ("Bengaluru" vs "Bengaluru rains: schools shut"). It is therefore used only when the
   shorter title has at least 3 meaningful words; shorter titles must be near-identical (ratio >= 90).
3. Page-monitor items ("<page title> updated") never join other stories by title: generic page titles
   ("Home updated") would merge unrelated pages.
4. Otherwise the item starts a new story.

A story keeps every item and every source that carried it. `source_count` (distinct sources) drives
scoring (step 5); `publisher_count` is kept too, because an X/Instagram backup query and a Google News
topic query can surface the same publisher's article.

Clusters live in memory here; step 6 stores them and feeds the last 48 hours back in on the next run.
Adapted from the panel project's fetcher/dedupe.py (copied, not imported - D-006).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache

from rapidfuzz import fuzz, process

from .normalise import Item, norm_title

THRESHOLD = 85.0
SHORT_TITLE_THRESHOLD = 90.0
MIN_SET_TOKENS = 3
WINDOW = timedelta(hours=48)
PAGE_MONITOR = "Web page monitor"
STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "from", "by", "with", "and", "or", "is", "are", "was",
    "be", "as", "after", "over", "its", "it", "this", "that", "will", "has", "have", "amid", "into", "s",
}  # fmt: skip


@lru_cache(maxsize=65536)
def _meaningful_count(norm: str) -> int:
    return len({t for t in norm.split() if t not in STOPWORDS})


def similarity(a: str, b: str) -> float:
    """How alike two normalised titles are, 0-100, with the short-title guard."""
    if not a or not b:
        return 0.0
    if min(_meaningful_count(a), _meaningful_count(b)) >= MIN_SET_TOKENS:
        return fuzz.token_set_ratio(a, b)
    ratio = fuzz.ratio(a, b)
    return ratio if ratio >= SHORT_TITLE_THRESHOLD else 0.0


@dataclass
class Member:
    item_id: str
    source_id: str
    publisher: str
    title: str
    norm_title: str
    published_at: datetime


@dataclass
class Cluster:
    cluster_id: str
    headline: str
    first_seen_at: datetime  # when the engine first saw the story
    members: list[Member] = field(default_factory=list)
    page_monitor: bool = False

    @property
    def sources(self) -> list[str]:
        return sorted({m.source_id for m in self.members})

    @property
    def source_count(self) -> int:
        return len(self.sources)

    @property
    def publisher_count(self) -> int:
        return len({m.publisher.lower() for m in self.members if m.publisher})


@dataclass
class AddResult:
    added: bool  # False: the item was already in a story (same item id)
    cluster: Cluster
    new_cluster: bool
    score: float = 0.0  # the title similarity that placed it, when it joined an existing story


class Clusterer:
    """Feed items in with add(); read the stories from `clusters`. Pass earlier stories (e.g. the last
    48 hours from the database) as `existing` so new items can join them."""

    def __init__(self, existing: list[Cluster] | None = None, *, threshold: float = THRESHOLD, window=WINDOW):
        self.threshold = threshold
        self.window = window
        self.clusters: dict[str, Cluster] = {}
        self._item_cluster: dict[str, str] = {}
        # every matchable title, flat, so rapidfuzz can scan them all in one fast call
        self._titles: list[str] = []
        self._refs: list[tuple[str, datetime]] = []  # (cluster_id, published_at), same order as _titles
        self._seen: dict[tuple[str, str], int] = {}  # (cluster_id, title) -> index, so a title is stored once
        for cluster in existing or []:
            self.clusters[cluster.cluster_id] = cluster
            for m in cluster.members:
                self._remember(cluster, m)

    def _remember(self, cluster: Cluster, m: Member) -> None:
        self._item_cluster[m.item_id] = cluster.cluster_id
        if cluster.page_monitor or not m.norm_title:
            return
        key = (cluster.cluster_id, m.norm_title)
        if key in self._seen:  # same title again in this story: keep the latest time, for the 48 h window
            idx = self._seen[key]
            self._refs[idx] = (cluster.cluster_id, max(self._refs[idx][1], m.published_at))
            return
        self._seen[key] = len(self._titles)
        self._titles.append(m.norm_title)
        self._refs.append((cluster.cluster_id, m.published_at))

    def _best_match(self, norm: str, published_at: datetime) -> tuple[Cluster | None, float]:
        # candidates by plain token_set_ratio (fast, in C), then the short-title guard and the time window
        hits = process.extract(norm, self._titles, scorer=fuzz.token_set_ratio, score_cutoff=self.threshold, limit=None)
        best, best_score = None, 0.0
        for title, raw, idx in hits:  # best raw score first
            if raw <= best_score:
                break  # the guard only ever lowers a score, so nothing further down can win
            cluster_id, when = self._refs[idx]
            if abs(when - published_at) > self.window:
                continue
            score = similarity(norm, title)
            if score >= self.threshold and score > best_score:
                best, best_score = self.clusters[cluster_id], score
        return best, best_score

    def add(self, item: Item, *, route_type: str = "") -> AddResult:
        if item.item_id in self._item_cluster:
            return AddResult(False, self.clusters[self._item_cluster[item.item_id]], False)
        norm = norm_title(item.title)
        member = Member(item.item_id, item.source_id, item.publisher, item.title, norm, item.published_at)
        is_page = route_type == PAGE_MONITOR
        cluster, score = (None, 0.0) if is_page or not norm else self._best_match(norm, item.published_at)
        new = cluster is None
        if new:
            cluster = Cluster(item.item_id, item.title, item.fetched_at, page_monitor=is_page)
            self.clusters[cluster.cluster_id] = cluster
        cluster.members.append(member)
        self._remember(cluster, member)
        return AddResult(True, cluster, new, score)

    def add_all(self, items: list[Item], route_types: dict[str, str] | None = None) -> list[AddResult]:
        """Add items oldest first, so each story's headline is the earliest report."""
        route_types = route_types or {}
        ordered = sorted(items, key=lambda i: i.published_at)
        return [self.add(i, route_type=route_types.get(i.source_id, "")) for i in ordered]
