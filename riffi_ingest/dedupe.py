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
   Titles are compared in a matching form (_canon): dates written one way ("3 October" = "Oct 3rd"; two
   different dates conflict like two different numbers), "Bangalore" = "Bengaluru", stop-words dropped,
   word forms stemmed ("extends" = "extended", "timings" = "timing"), and "Bengaluru" counts as said by a
   title naming a Bengaluru-only body ("Namma Metro", "BBMP"). The same story told in other words
   ("services to run beyond midnight" / "timings extended") still splits: that needs meaning, not titles.
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
DETAIL_RE = re.compile(r"^(\d+|\d\d-\d\d)$")  # numbers and dates (see _canon) must agree on both sides
MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}  # fmt: skip
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DAY = r"(?P<{0}>[12]\d|3[01]|0?[1-9])(?:st|nd|rd|th)?"
# "october 3", "oct 3rd", "3 october", "3rd of oct" -> "10-03". Day-first skips "may" ("3 may be hurt").
DATE_RE = re.compile(
    rf"\b(?:(?P<m1>{_MONTH}) {_DAY.format('d1')}|{_DAY.format('d2')} (?:of )?(?P<m2>{_MONTH.replace('may|', '')}))\b"
)
HOME = "bengaluru"
HOME_ALIASES = {"bangalore": HOME, "blr": HOME}
# bodies that exist only in Bengaluru: a title naming one already says "Bengaluru"
HOME_MARKERS = {"namma", "bbmp", "bmtc", "bmrcl", "bwssb", "bescom", "bda"}
STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "from", "by", "with", "and", "or", "is", "are", "was",
    "be", "as", "after", "over", "its", "it", "this", "that", "will", "has", "have", "amid", "into", "s",
}  # fmt: skip


def _stem(word: str) -> str:
    """Light English stemming so word forms compare equal: extends / extended / extending -> extend,
    timings / timing -> tim, hikes / hiked / hike -> hik. Only plain Latin words of 4+ letters; never
    leaves fewer than 3 letters. Kannada, numbers and dates are untouched."""
    if len(word) < 4 or not word.isascii() or not word.isalpha():
        return word
    if word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 4:
        word = word[:-1]
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    return word


@lru_cache(maxsize=65536)
def _canon(norm: str) -> str:
    """The form titles are matched in: dates written one way ("3 October", "Oct 3rd" -> "10-03"),
    "Bangalore" -> "bengaluru", stop-words dropped, each word stemmed, and "bengaluru" dropped from a
    title that names a Bengaluru-only body."""
    norm = DATE_RE.sub(lambda m: f"{MONTHS[m['m1'] or m['m2']]:02d}-{int(m['d1'] or m['d2']):02d}", norm)
    words = [_stem(HOME_ALIASES.get(t, t)) for t in norm.split() if t not in STOPWORDS]
    if HOME_MARKERS.intersection(words):  # "Namma Metro ... in Bengaluru": the city adds nothing
        words = [w for w in words if w != HOME]
    return " ".join(words)


@lru_cache(maxsize=65536)
def _words(key: str) -> frozenset[str]:
    return frozenset(t for t in key.split() if t not in STOPWORDS)


def _unmatched(mine: frozenset[str], theirs: frozenset[str]) -> bool:
    """True if `mine` has a non-number word with no near-match in `theirs`. "Bengaluru" counts as
    matched when `theirs` names a Bengaluru-only body ("Namma Metro", "BBMP")."""
    if theirs & HOME_MARKERS:
        theirs = theirs | {HOME}
    return any(not DETAIL_RE.match(w) and not any(fuzz.ratio(w, t) >= NEAR_WORD for t in theirs) for w in mine - theirs)


def _conflicting(ka: str, kb: str) -> bool:
    wa, wb = _words(ka), _words(kb)
    na, nb = {w for w in wa if DETAIL_RE.match(w)}, {w for w in wb if DETAIL_RE.match(w)}
    if na and nb and na != nb:
        return True
    return _unmatched(wa, wb) and _unmatched(wb, wa)


def _coverage(ka: str, kb: str) -> float:
    la, lb = len(_words(ka)), len(_words(kb))
    return min(la, lb) / max(la, lb) if max(la, lb) else 0.0


def _similarity(ka: str, kb: str) -> float:
    """similarity() on titles already in matching form (_canon). Never higher than their
    token_set_ratio, which the candidate scan relies on."""
    if not ka or not kb:
        return 0.0
    score = fuzz.token_set_ratio(ka, kb)
    if score < THRESHOLD or _coverage(ka, kb) < MIN_COVERAGE or _conflicting(ka, kb):
        return 0.0
    return score


def conflicting(a: str, b: str) -> bool:
    return _conflicting(_canon(a), _canon(b))


def coverage(a: str, b: str) -> float:
    return _coverage(_canon(a), _canon(b))


def similarity(a: str, b: str) -> float:
    """How alike two normalised titles are as the same story, 0-100 (0 when they must not merge)."""
    return _similarity(_canon(a), _canon(b))


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
        self._titles.append(_canon(m.norm_title))
        self._title_cluster.append(cluster.cluster_id)

    def _best_match(self, norm: str, published_at: datetime) -> tuple[Cluster | None, float]:
        # candidates by plain token_set_ratio (fast, in C); similarity() is never higher than that score
        key = _canon(norm)
        hits = process.extract(key, self._titles, scorer=fuzz.token_set_ratio, score_cutoff=self.threshold, limit=None)
        best, best_score = None, 0.0
        for title, raw, idx in hits:  # best raw score first
            if raw <= best_score:
                break
            cluster_id = self._title_cluster[idx]
            if abs(published_at - self._started[cluster_id]) > self.window:
                continue
            cluster = self.clusters[cluster_id]
            score = _similarity(key, title)
            if score > best_score:
                best, best_score = cluster, score
        return best, best_score

    def add(self, item: Item, *, route_type: str = "") -> AddResult:
        if item.item_id in self._item_cluster:
            return AddResult(False, self.clusters[self._item_cluster[item.item_id]], False)
        published_at = _aware(item.published_at or item.fetched_at)
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
        ordered = sorted(items, key=lambda i: _aware(i.published_at or i.fetched_at))
        return [self.add(i, route_type=route_types.get(i.source_id, "")) for i in ordered]
