"""Tag stories and Reddit posts with topics (catalog/topics.yaml).

This replaces the Google News gap queries: the same questions ("what is Bengaluru saying
about rent?") answered from the publisher feeds and Reddit posts we already store.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from feedkit.parse import iso, utcnow

from .config import ROOT

TOPICS_FILE = ROOT / "catalog" / "topics.yaml"
LOCAL_AREAS = {"Bangalore & Karnataka"}


@dataclass
class Topic:
    key: str
    label: str
    pattern: re.Pattern
    local: bool = False
    filter: bool = False
    replaces: tuple = ()


class TopicTagger:
    def __init__(self, path: Path = TOPICS_FILE):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.local_re = re.compile(data.get("local_context", "bengaluru|bangalore|karnataka"), re.I)
        self.topics = [
            Topic(
                key=k,
                label=t["label"],
                pattern=re.compile(t["pattern"], re.I),
                local=bool(t.get("local", False)),
                filter=bool(t.get("filter", False)),
                replaces=tuple(t.get("replaces", []) or []),
            )
            for k, t in (data.get("topics") or {}).items()
        ]

    @property
    def filters(self) -> list[Topic]:
        return [t for t in self.topics if t.filter]

    def is_local(self, text: str, subject_area: str = "", local_hint: bool | None = None) -> bool:
        """local_hint (publisher items): the feed is a Bengaluru / Karnataka desk. Without a hint
        (Reddit posts), the subreddit's area decides. Either way, naming the place counts."""
        if self.local_re.search(text):
            return True
        return bool(local_hint) if local_hint is not None else subject_area in LOCAL_AREAS

    def tags(self, text: str, subject_area: str = "", local_hint: bool | None = None) -> list[str]:
        text = text or ""
        local = None
        out = []
        for t in self.topics:
            if not t.pattern.search(text):
                continue
            if t.local:
                if local is None:
                    local = self.is_local(text, subject_area, local_hint)
                if not local:
                    continue
            out.append(t.key)
        return out


IST_OFFSET_SQL = "'+5 hours', '+30 minutes'"
RESULTS_FILE = ROOT / "output" / "state" / "results.json"
REPORT_FILE = ROOT / "output" / "topic_filters_report.md"


def _gn_baseline(ids: tuple) -> list[dict]:
    """What the old Google News queries returned in their live check (26-27 Sep 2026)."""
    if not RESULTS_FILE.exists():
        return []
    results = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    out = []
    for sid in ids:
        rec = results.get(sid) or {}
        check = rec.get("phase1") or {}
        if (rec.get("phase2") or {}).get("result"):
            check = rec["phase2"]["result"]
        url = check.get("url") or (rec.get("row") or {}).get("feed_url", "")
        m = re.search(r"when:(\d+)d", url)
        days = int(m.group(1)) if m else None
        entries = check.get("entries_count") or 0
        out.append(
            {
                "id": sid,
                "entries": entries,
                "window_days": days,
                "checked_utc": check.get("checked_utc", ""),
                "per_day": round(entries / days, 1) if days else None,
                "is_reddit": "reddit.com" in url,
            }
        )
    return out


def topic_report(db, days: int = 3, examples: int = 4) -> str:
    """Stories per topic per day (IST), publisher vs Reddit, against the old Google News counts."""
    tagger = TopicTagger()
    day_sql = f"date(published_utc, {IST_OFFSET_SQL})"
    today = db.conn.execute(f"SELECT date('now', {IST_OFFSET_SQL})").fetchone()[0]
    first_day = db.conn.execute(f"SELECT date('now', {IST_OFFSET_SQL}, '-{days - 1} days')").fetchone()[0]
    day_list = [
        r[0]
        for r in db.conn.execute(
            "WITH RECURSIVE d(x) AS (SELECT ?1 UNION ALL SELECT date(x, '+1 day') FROM d WHERE x < ?2) SELECT x FROM d",
            (first_day, today),
        )
    ]
    lines = [
        "# Keyword filters: stories per topic per day",
        "",
        f"Generated {iso(utcnow())} from data/feeds.db. Days are IST calendar days; {today} is partial (up to now).",
        "Publisher = stories from the verified publisher feeds (Google News off). Reddit = posts from the enabled",
        "subreddits (trend signal). A story counts once per topic. Bengaluru topics count only local stories.",
        "",
        "Google News baseline: what the old query returned in its one live check on 26-27 Sep 2026, divided by its",
        "`when:` window. It is a single snapshot, not a daily series, and it searched all Indian outlets.",
        "",
        "| Topic | "
        + " | ".join(f"{d} pub / reddit" for d in day_list)
        + " | Last 24h pub / reddit | Old Google News (per day) |",
        "|---|" + "---:|" * (len(day_list) + 2),
    ]
    detail = []
    for t in tagger.filters:
        like = f'%"{t.key}"%'
        pub_days = dict(
            db.conn.execute(
                f"SELECT {day_sql} d, COUNT(*) FROM items WHERE topics LIKE ? AND {day_sql} >= ? GROUP BY d",
                (like, first_day),
            ).fetchall()
        )
        red_days = dict(
            db.conn.execute(
                f"SELECT {day_sql} d, COUNT(*) FROM reddit_posts WHERE topics LIKE ? AND {day_sql} >= ? GROUP BY d",
                (like, first_day),
            ).fetchall()
        )
        pub24 = db.conn.execute(
            "SELECT COUNT(*) FROM items WHERE topics LIKE ? AND published_utc >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day')",
            (like,),
        ).fetchone()[0]
        red24 = db.conn.execute(
            "SELECT COUNT(*) FROM reddit_posts WHERE topics LIKE ? AND published_utc >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day')",
            (like,),
        ).fetchone()[0]
        gn = _gn_baseline(t.replaces)
        gn_txt = (
            "; ".join(
                f"{g['id']}{' (Reddit search)' if g['is_reddit'] else ''}: {g['entries']} in {g['window_days'] or '?'}d"
                + (f" = {g['per_day']}/day" if g["per_day"] is not None else "")
                for g in gn
            )
            or "-"
        )
        cells = " | ".join(f"{pub_days.get(d, 0)} / {red_days.get(d, 0)}" for d in day_list)
        lines.append(f"| {t.label} | {cells} | {pub24} / {red24} | {gn_txt} |")
        rows = db.conn.execute(
            """SELECT source_name, title, published_utc FROM items WHERE topics LIKE ?
               AND published_utc >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day') ORDER BY published_utc DESC LIMIT ?""",
            (like, examples),
        ).fetchall()
        reds = db.conn.execute(
            """SELECT subreddit, title FROM reddit_posts WHERE topics LIKE ?
               AND published_utc >= strftime('%Y-%m-%dT%H:%M:%SZ','now','-1 day') ORDER BY published_utc DESC LIMIT 2""",
            (like,),
        ).fetchall()
        detail.append(f"### {t.label}")
        detail += [f"- {r['source_name']}: {r['title']}" for r in rows] or ["- (no publisher stories in the last 24h)"]
        detail += [f"- r/{r['subreddit']}: {r['title']}" for r in reds]
        detail.append("")
    lines += ["", "## Examples from the last 24 hours (to judge precision)", ""] + detail
    text = "\n".join(lines) + "\n"
    REPORT_FILE.write_text(text, encoding="utf-8")
    return text


def retag_all(db, tagger: TopicTagger | None = None) -> dict:
    """Re-tag every stored story and Reddit post (after the topic file changes)."""
    tagger = tagger or TopicTagger()
    counts = {"items": 0, "reddit": 0}
    with db.conn:
        rows = db.conn.execute(
            "SELECT i.id, i.title, COALESCE(i.title_en, '') AS title_en, i.summary, i.subject_area, "
            "COALESCE(f.local, 0) AS local FROM items i LEFT JOIN feeds f ON f.feed_id = i.feed_id"
        ).fetchall()
        for r in rows:  # a Kannada headline's English translation counts too
            tags = tagger.tags(
                f"{r['title']} {r['title_en']} {r['summary'] or ''}", r["subject_area"] or "", bool(r["local"])
            )
            db.conn.execute("UPDATE items SET topics = ? WHERE id = ?", (json.dumps(tags), r["id"]))
            counts["items"] += 1
        rows = db.conn.execute("SELECT post_id, title, subject_area FROM reddit_posts").fetchall()
        for r in rows:
            tags = tagger.tags(r["title"], r["subject_area"] or "")
            db.conn.execute("UPDATE reddit_posts SET topics = ? WHERE post_id = ?", (json.dumps(tags), r["post_id"]))
            counts["reddit"] += 1
    return counts
