"""Near-duplicate stories. The clusterer already merges reports that are the same story; these are different
clusters that tell the same news in other words ("BBMP seals 14 PGs" / "Officials seal 14 PG accommodations").
Before the selection, such stories are treated as one and the one with more sources stays (ties: the higher pick
score, then the newer, then the lower id).

Two stories are the same when their headlines share at least `similar.jaccard` of their content words, or - for
stories on the same topic and the same IST day - when they share enough names / numbers / acronyms, or enough
content words with a similar overall wording. Everything is in config/filter.yaml."""

from __future__ import annotations

import re
from collections import defaultdict

from ..dedupe import canon_word, title_words
from ..fetchers.parse import IST
from .config import FilterConfig
from .model import Assessed

TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def entities(headline: str, generic: set[str]) -> set[str]:
    """Names, numbers and acronyms of a headline: capitalised words (not the first word, which is capitalised
    anyway, unless it is an acronym or a number), all-caps words and digits; in the stemmed form."""
    out = set()
    for i, token in enumerate(TOKEN.findall(headline or "")):
        keep = token.isdigit() or (token.isupper() and len(token) >= 2) or (i > 0 and token[:1].isupper())
        if keep:
            word = canon_word(token)
            if word and word not in generic:
                out.add(word)
    return out


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


def same_story(a: Assessed, b: Assessed, cfg: FilterConfig) -> bool:
    s = cfg["similar"]
    wa, wb = title_words(a.story.headline), title_words(b.story.headline)
    if _jaccard(wa, wb) >= float(s["jaccard"]):
        return True
    same_day = a.story.started_at.astimezone(IST).date() == b.story.started_at.astimezone(IST).date()
    if not same_day or a.topic.topic_id != b.topic.topic_id:
        return False
    generic_e = {str(x).lower() for x in s["generic_entities"]}
    if len(entities(a.story.headline, generic_e) & entities(b.story.headline, generic_e)) >= int(
        s["same_day_shared_entities"]
    ):
        return True
    generic_w = {canon_word(str(x)) for x in s["generic_words"]}
    shared = (wa & wb) - generic_w
    return len(shared) >= int(s["same_day_shared_words"]) and _jaccard(wa, wb) >= float(s["jaccard_floor"])


def _rank(a: Assessed):
    return (-a.story.source_count, -a.p, -a.story.started_at.timestamp(), a.story.story_id)


def near_duplicates(candidates: list[Assessed], cfg: FilterConfig) -> dict[str, str]:
    """{story_id of the one to drop: story_id of the one kept}. Compared within a category only."""
    parent: dict[str, str] = {a.story.story_id: a.story.story_id for a in candidates}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_category: dict[str, list[Assessed]] = defaultdict(list)
    for a in candidates:
        by_category[a.category].append(a)
    for group in by_category.values():
        group.sort(key=lambda a: a.story.story_id)
        for i, a in enumerate(group):
            for b in group[i + 1 :]:
                if same_story(a, b, cfg):
                    parent[find(a.story.story_id)] = find(b.story.story_id)
    clusters: dict[str, list[Assessed]] = defaultdict(list)
    for a in candidates:
        clusters[find(a.story.story_id)].append(a)
    dropped = {}
    for members in clusters.values():
        if len(members) > 1:
            keep = sorted(members, key=_rank)[0]
            dropped.update({m.story.story_id: keep.story.story_id for m in members if m is not keep})
    return dropped
