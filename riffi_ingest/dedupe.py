"""Step 3 (BRIEF.md "STEP 3: DE-DUPLICATE INTO STORY CLUSTERS"): group items into stories.

1. Same item id (same canonical URL) = the same item, stored once.
2. A title that matches a title already in a story joins that story, if it was published within 48 hours
   of the story's first report. "Matches" means all three of:
   - rapidfuzz token_set_ratio >= 85 on the normalised titles (the brief's rule);
   - no conflicting detail: not two different sets of numbers ("5 killed" / "12 killed", "October 3" /
     "October 4"), and not a word on each side with no near-match on the other ("BJP" / "Congress",
     "Delhi" / "Bengaluru", "today" / "tomorrow"; "floats" / "floated" are near-matches);
   - the shorter title holds at least 70% as many meaningful words as the longer one, so a title cannot
     swallow every longer story that contains it ("Metro fare hike" / "Metro fare hike rolled back").
   token_set_ratio alone scores 100 whenever one title's words all sit inside the other, which is what
   the last two rules stop. They were chosen on a labelled set of Bengaluru headline pairs (see
   tests/test_dedupe.py): all same-story pairs merge, no different-story pair does.
3. The 48 hours count from the story's first report, so recurring items ("Gold rate today") start a new
   story each time instead of chaining into one that never ends.
4. Page-monitor items ("<page title> updated") never join other stories by title: generic page titles
   ("Home updated") would merge unrelated pages.
5. Otherwise the item starts a new story.

A story keeps every item and every source that carried it. `source_count` (distinct sources) drives
scoring (step 5); `publisher_count` is kept too, because an X/Instagram backup query and a Google News
topic query can surface the same publisher's article.

Clusters live in memory here; step 6 stores them and feeds the last 48 hours back in on the next run.
Adapted from the panel project's fetcher/dedupe.py (copied, not imported - D-006).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from rapidfuzz import fuzz, process

from .normalise import Item, norm_title

THRESHOLD = 85.0
MIN_COVERAGE = 0.7
NEAR_WORD = 75.0  # two words this alike are the same word in another form ("floats" / "floated")
WINDOW = timedelta(hours=48)
PAGE_MONITOR = "Web page monitor"
NUMBER_RE = re.compile(r"^\d+$")
STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "from", "by", "with", "and", "or", "is", "are", "was",
    "be", "as", "after", "over", "its", "it", "this", "that", "will", "has", "have", "amid", "into", "s",
}  # fmt: skip


@lru_cache(maxsize=65536)
def _words(norm: str) -> frozenset[str]:
    return frozenset(t for t in norm.split() if t not in STOPWORDS)


def _unmatched(mine: frozenset[str], theirs: frozenset[str]) -> bool:
    """True if `mine` has a non-number word with no near-match in `theirs`."""
    return any(not NUMBER_RE.match(w) and not any(fuzz.ratio(w, t) >= NEAR_WORD for t in theirs) for w in mine - theirs)


def conflicting(a: str, b: str) -> bool:
    wa, wb = _words(a), _words(b)
    na, nb = {w for w in wa if NUMBER_RE.match(w)}, {w for w in wb if NUMBER_RE.match(w)}
    if na and nb and na != nb:
        return True
    return _unmatched(wa, wb) and _unmatched(wb, wa)


def coverage(a: str, b: str) -> float:
    la, lb = len(_words(a)), len(_words(b))
    return min(la, lb) / max(la, lb) if max(la, lb) else 0.0


def similarity(a: str, b: str) -> float:
    """How alike two normalised titles are as the same story, 0-100 (0 when they must not merge).
    Never higher than token_set_ratio, which the candidate scan relies on."""
    if not a or not b:
        return 0.0
    score = fuzz.token_set_ratio(a, b)
    if score < THRESHOLD or coverage(a, b) < MIN_COVERAGE or conflicting(a, b):
        return 0.0
    return score


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def story_title(title: str | None, publisher: str) -> str:
    """The title to compare: normalised, without a trailing " - Publisher" / " | Publisher"."""
    title = title or ""
    if publisher:
        title = re.sub(rf"\s+[-–—|]\s+{re.escape(publisher)}\s*$", "", title, flags=re.I)
    return norm_title(title)


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
    def started_at(self) -> datetime:
        """The first report's publication time: the story's 48 h window counts from here."""
        return min(_aware(m.published_at) for m in self.members)

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
        self._title_cluster: list[str] = []  # cluster_id, same order as _titles
        self._seen: set[tuple[str, str]] = set()  # (cluster_id, title): each title stored once per story
        self._started: dict[str, datetime] = {}  # cluster_id -> first report's time (the window's anchor)
        for cluster in existing or []:
            self.clusters[cluster.cluster_id] = cluster
            for m in cluster.members:
                self._remember(cluster, m)

    def _remember(self, cluster: Cluster, m: Member) -> None:
        self._item_cluster[m.item_id] = cluster.cluster_id
        when = _aware(m.published_at)
        self._started[cluster.cluster_id] = min(self._started.get(cluster.cluster_id, when), when)
        key = (cluster.cluster_id, m.norm_title)
        if cluster.page_monitor or not m.norm_title or key in self._seen:
            return
        self._seen.add(key)
        self._titles.append(m.norm_title)
        self._title_cluster.append(cluster.cluster_id)

    def _best_match(self, norm: str, published_at: datetime) -> tuple[Cluster | None, float]:
        # candidates by plain token_set_ratio (fast, in C); similarity() is never higher than that score
        hits = process.extract(norm, self._titles, scorer=fuzz.token_set_ratio, score_cutoff=self.threshold, limit=None)
        best, best_score = None, 0.0
        for title, raw, idx in hits:  # best raw score first
            if raw <= best_score:
                break
            cluster_id = self._title_cluster[idx]
            if abs(published_at - self._started[cluster_id]) > self.window:
                continue
            cluster = self.clusters[cluster_id]
            score = similarity(norm, title)
            if score > best_score:
                best, best_score = cluster, score
        return best, best_score

    def add(self, item: Item, *, route_type: str = "") -> AddResult:
        if item.item_id in self._item_cluster:
            return AddResult(False, self.clusters[self._item_cluster[item.item_id]], False)
        published_at = _aware(item.published_at)
        norm = story_title(item.title, item.publisher)
        member = Member(item.item_id, item.source_id, item.publisher, item.title or "", norm, published_at)
        is_page = route_type == PAGE_MONITOR
        cluster, score = (None, 0.0) if is_page or not norm else self._best_match(norm, published_at)
        new = cluster is None
        if new:
            cluster = Cluster(item.item_id, item.title or "", _aware(item.fetched_at), page_monitor=is_page)
            self.clusters[cluster.cluster_id] = cluster
        cluster.members.append(member)
        self._remember(cluster, member)
        return AddResult(True, cluster, new, score)

    def add_all(self, items: list[Item], route_types: dict[str, str] | None = None) -> list[AddResult]:
        """Add items oldest first, so each story's headline is the earliest report."""
        route_types = route_types or {}
        ordered = sorted(items, key=lambda i: _aware(i.published_at))
        return [self.add(i, route_type=route_types.get(i.source_id, "")) for i in ordered]
