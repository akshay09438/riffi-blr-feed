"""Deduplicate and cluster items.

1. Same canonical URL  -> the same item (stored once).
2. With the local embedding model (the default when models/ is present): the story joins the
   active cluster with the most similar meaning (fetcher/semantic.py), so one story from ten
   outlets is one cluster even when the headlines are worded differently.
3. Without the model: a near-identical title (rapidfuzz ratio > threshold) within the window
   joins the same cluster.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from rapidfuzz import fuzz, process

from feedkit.parse import iso

from .db import DB
from .embed import to_blob
from .normalize import norm_title


class Clusterer:
    def __init__(
        self,
        db: DB,
        *,
        threshold: float = 90.0,
        window_hours: int = 48,
        now: datetime,
        embedder=None,
        semantic_threshold: float | None = None,
    ):
        self.db = db
        self.threshold = threshold
        self.window = timedelta(hours=window_hours)
        self.embedder = embedder
        self.index = None
        if embedder is not None:
            from .semantic import DEFAULT_THRESHOLD, SemanticIndex

            self.index = SemanticIndex.from_db(db, semantic_threshold or DEFAULT_THRESHOLD, now)
            return
        since = iso(now - self.window)
        self._ids: list[int] = []
        self._titles: list[str] = []
        for row in db.recent_clusters(since):
            self._ids.append(row["cluster_id"])
            self._titles.append(row["norm_title"])

    def match(self, title: str) -> int | None:
        if not self._titles:
            return None
        hit = process.extractOne(norm_title(title), self._titles, scorer=fuzz.ratio, score_cutoff=self.threshold + 1e-9)
        return self._ids[hit[2]] if hit else None

    def add(self, item: dict, url_resolved: bool = True) -> tuple[bool, int]:
        """Store an item; returns (added, cluster_id). Duplicate URLs are skipped."""
        if self.db.has_item(item["canonical_url"]):
            return False, -1
        if self.index is not None:
            return self._add_semantic(item, url_resolved)
        cluster_id = self.match(item["title"])
        if cluster_id is None:
            norm = norm_title(item["title"])
            cluster_id = self.db.new_cluster(item, norm)
            self._ids.append(cluster_id)
            self._titles.append(norm)
        added = self.db.add_item(item, cluster_id, url_resolved)
        return added, cluster_id

    def _add_semantic(self, item: dict, url_resolved: bool) -> tuple[bool, int]:
        from .semantic import _dt, index_lang, semantic_text, text_hash

        when = _dt(item["published_utc"])
        text = semantic_text(item.get("language"), item["title"], item.get("title_en"))
        lang = index_lang(item.get("language"), text is not None)
        vec = self.embedder.encode([text])[0] if text is not None else None
        title = text or item["title"]
        cluster_id, _score, _how = self.index.match(vec, title, when, lang)
        if cluster_id is None:
            cluster_id = self.db.new_cluster(item, norm_title(item["title"]))
        self.index.add(cluster_id, vec, title, when, lang=lang)
        added = self.db.add_item(item, cluster_id, url_resolved)
        if added and vec is not None:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO vectors (ref, model, vec, text_hash) VALUES (?, ?, ?, ?)",
                (f"item:{item['id']}", self.embedder.name, to_blob(vec), text_hash(text)),
            )
            self.db.conn.execute(
                "UPDATE clusters SET centroid = ?, n_vec = ? WHERE cluster_id = ?",
                (to_blob(self.index.centroid(cluster_id)), self.index.count(cluster_id), cluster_id),
            )
        return added, cluster_id
