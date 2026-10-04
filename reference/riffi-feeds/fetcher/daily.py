"""Daily upkeep for a 24/7 server: the health summary, data retention and log rotation.

The `run` loop calls daily_jobs() once a day at 7:00 IST, right after the shortlist; each piece
also has a command (`python -m fetcher daily-summary`, `python -m fetcher prune`).

Retention keeps the database small on a small server: headlines and teasers for
retention.items_days (default 90), Reddit posts for retention.reddit_days (30), and the
meaning vectors, which only serve the 48-hour grouping window, for retention.vectors_days (14).
Set a value to 0 to keep that forever. Daily shortlists are files and are never pruned.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from feedkit.parse import iso, utcnow

from .config import HEALTH_LOG, ROOT, Settings

log = logging.getLogger("fetcher")
HEALTH_DIR = ROOT / "output" / "health"
SHORTLIST_DIR = ROOT / "output" / "shortlists"
HEARTBEAT = ROOT / "output" / "state" / "heartbeat"
DEFAULT_RETENTION = {"items_days": 90, "reddit_days": 30, "vectors_days": 14}
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_KEEP = 5


def ist_day(now: datetime) -> str:
    return (now + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def beat(path: Path = HEARTBEAT) -> None:
    """Touched every loop; the Docker / systemd health check reads its age."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(iso(utcnow()), encoding="utf-8")


def rotate(path: Path, max_bytes: int = LOG_MAX_BYTES, keep: int = LOG_KEEP) -> bool:
    """feed_health.log -> .1 -> .2 ... when it passes max_bytes; the oldest falls off."""
    if not path.exists() or path.stat().st_size < max_bytes:
        return False
    for n in range(keep - 1, 0, -1):
        older = path.with_name(f"{path.name}.{n}")
        if older.exists():
            os.replace(older, path.with_name(f"{path.name}.{n + 1}"))
    os.replace(path, path.with_name(f"{path.name}.1"))
    stale = path.with_name(f"{path.name}.{keep + 1}")
    if stale.exists():
        stale.unlink()
    return True


def rotate_logs(paths: list[Path] | None = None) -> list[str]:
    from .reddit_signal import REDDIT_LOG

    return [p.name for p in (paths or [HEALTH_LOG, REDDIT_LOG]) if rotate(p)]


def prune(db, now: datetime, retention: dict | None = None, vacuum: bool = False) -> dict:
    """Delete what is past its retention, oldest data only; never touches the last 48 hours."""
    keep = {**DEFAULT_RETENTION, **(retention or {})}
    out = {"items": 0, "reddit_posts": 0, "vectors": 0, "clusters": 0, "centroids": 0}
    floor = now - timedelta(days=3)  # grouping and Reddit attachment look back 48-72h

    def cutoff(days: int) -> str | None:
        return iso(min(now - timedelta(days=days), floor)) if days and days > 0 else None

    with db.conn:
        db.conn.execute("PRAGMA defer_foreign_keys = ON")
        if (c := cutoff(keep["vectors_days"])) is not None:
            out["vectors"] += db.conn.execute(
                "DELETE FROM vectors WHERE ref LIKE 'item:%' AND substr(ref, 6) IN "
                "(SELECT id FROM items WHERE published_utc < ?)",
                (c,),
            ).rowcount
            out["vectors"] += db.conn.execute(
                "DELETE FROM vectors WHERE ref LIKE 'reddit:%' AND substr(ref, 8) IN "
                "(SELECT post_id FROM reddit_posts WHERE published_utc < ?)",
                (c,),
            ).rowcount
            # a story's meaning vector is only used while it can still gather items (48 h) or be
            # compared by the picker (36 h): older ones only take space (about a third of the file)
            out["centroids"] = db.conn.execute(
                "UPDATE clusters SET centroid = NULL, n_vec = 0 WHERE centroid IS NOT NULL AND last_published_utc < ?",
                (c,),
            ).rowcount
        from . import reddit_signal

        # while Reddit is ceased the stored posts are kept until the founder decides to delete them
        # (founder, 1 Oct 2026, option A): nothing new arrives, so nothing ages out
        if not reddit_signal.CEASED and (c := cutoff(keep["reddit_days"])) is not None:
            out["reddit_posts"] = db.conn.execute("DELETE FROM reddit_posts WHERE published_utc < ?", (c,)).rowcount
        if (c := cutoff(keep["items_days"])) is not None:
            out["items"] = db.conn.execute("DELETE FROM items WHERE published_utc < ?", (c,)).rowcount
            out["vectors"] += db.conn.execute(
                "DELETE FROM vectors WHERE ref LIKE 'item:%' AND substr(ref, 6) NOT IN (SELECT id FROM items)"
            ).rowcount
            db.conn.execute(
                "UPDATE reddit_posts SET cluster_id = NULL, attach_method = NULL, attach_score = NULL "
                "WHERE cluster_id IS NOT NULL AND cluster_id NOT IN (SELECT cluster_id FROM items)"
            )
            out["clusters"] = db.conn.execute(
                "DELETE FROM clusters WHERE cluster_id NOT IN (SELECT cluster_id FROM items)"
            ).rowcount
    if vacuum and any(out.values()):
        db.conn.execute("VACUUM")
        out["vacuumed"] = True
    return out


def health_summary(db, settings: Settings, now: datetime, db_path: str | Path | None = None) -> dict:
    """The last 24 hours in one page: are feeds, Reddit, Google News (thin topics),
    translation and storage all healthy? Warnings are listed first."""
    from feedkit.net import contact_is_placeholder as net_contact_placeholder

    from .poller import eligible
    from .reddit_signal import REDDIT_LOG

    since = iso(now - timedelta(hours=24))
    eligible_ids = {f.id for f in settings.feeds if eligible(settings, f)[0]}
    states = {r["feed_id"]: dict(r) for r in db.conn.execute("SELECT * FROM feeds")}
    live = [states[i] for i in eligible_ids if i in states]
    polled = [s for s in live if (s["last_polled_utc"] or "") >= since]
    failing = sorted((s for s in live if s["consecutive_failures"] >= 3), key=lambda s: -s["consecutive_failures"])
    status = {}
    for s in live:
        status[s["status"] or "not checked"] = status.get(s["status"] or "not checked", 0) + 1
    p0_down = [s for s in live if s["priority"] == "P0" and s["status"] in ("BROKEN", "STALE")]
    added = dict(
        db.conn.execute(
            "SELECT subject_area, COUNT(*) FROM items WHERE fetched_utc >= ? GROUP BY subject_area", (since,)
        ).fetchall()
    )
    by_type = dict(
        db.conn.execute(
            "SELECT source_type, COUNT(*) FROM items WHERE fetched_utc >= ? GROUP BY source_type", (since,)
        ).fetchall()
    )
    kn_total, kn_done = db.conn.execute(
        "SELECT COUNT(*), SUM(COALESCE(title_en, '') != '') FROM items WHERE language = 'KN' AND fetched_utc >= ?",
        (since,),
    ).fetchone()
    gnt = [
        {
            "feed": s["feed_id"],
            "last_polled": s["last_polled_utc"],
            "added_total": s["items_added"],
            "last_error": s["last_error"],
        }
        for s in live
        if s["feed_id"].startswith("GNT-")
    ]
    reddit_posts = db.conn.execute("SELECT COUNT(*) FROM reddit_posts WHERE first_seen_utc >= ?", (since,)).fetchone()[
        0
    ]
    paused = db.conn.execute("SELECT value FROM reddit_state WHERE key = 'paused_until'").fetchone()
    blocks = 0
    if REDDIT_LOG.exists():
        with REDDIT_LOG.open(encoding="utf-8", errors="replace") as fh:
            blocks = sum(1 for line in fh if line[:20] >= since and " BLOCKED " in line)
    db_file = Path(db_path) if db_path else None
    disk = shutil.disk_usage(str(ROOT))
    shortlist = SHORTLIST_DIR / f"{ist_day(now)}.json"
    warnings = []
    if net_contact_placeholder(settings.bot_contact):
        warnings.append("bot contact is still a placeholder (defaults.bot_contact or FEEDBOT_CONTACT)")
    if p0_down:
        warnings.append(f"{len(p0_down)} priority feeds broken or stale: " + ", ".join(s["feed_id"] for s in p0_down))
    if len(polled) < 0.8 * len(live):
        warnings.append(f"only {len(polled)} of {len(live)} feeds polled in 24h")
    if blocks:
        warnings.append(f"Reddit blocked us {blocks} time(s) in 24h (all Reddit requests paused each time)")
    if kn_total and (kn_done or 0) < 0.9 * kn_total:
        warnings.append(f"only {kn_done or 0} of {kn_total} new Kannada headlines translated")
    if disk.free < 2 * 1024**3:
        warnings.append(f"disk nearly full: {disk.free / 1024**3:.1f} GB free")
    if not shortlist.exists() and (now + timedelta(hours=5, minutes=30)).hour >= 8:
        warnings.append(f"no shortlist for {ist_day(now)}")
    return {
        "date_ist": ist_day(now),
        "generated_utc": iso(now),
        "status": "warn" if warnings else "ok",
        "warnings": warnings,
        "feeds": {
            "eligible": len(live),
            "polled_24h": len(polled),
            "status": status,
            "failing_3plus": [
                {
                    "feed": s["feed_id"],
                    "name": s["source_name"],
                    "fails": s["consecutive_failures"],
                    "error": s["last_error"],
                }
                for s in failing[:15]
            ],
        },
        "items_added_24h": {"total": sum(added.values()), "by_area": added, "by_source_type": by_type},
        "kannada_24h": {"new": kn_total, "translated": kn_done or 0},
        "google_news_thin_topics": gnt,
        "reddit": {
            "new_posts_24h": reddit_posts,
            "blocks_24h": blocks,
            "paused_until": paused["value"] if paused and paused["value"] > iso(now) else None,
        },
        "storage": {
            "db_mb": round(db_file.stat().st_size / 1024**2, 1) if db_file and db_file.exists() else None,
            "disk_free_gb": round(disk.free / 1024**3, 1),
        },
        "shortlist": str(shortlist) if shortlist.exists() else None,
    }


def write_health_summary(summary: dict, out_dir: Path = HEALTH_DIR, log_path: Path = HEALTH_LOG) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / f"{summary['date_ist']}.md"
    (out_dir / f"{summary['date_ist']}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    f, items = summary["feeds"], summary["items_added_24h"]
    lines = [
        f"# Fetcher health - {summary['date_ist']} ({summary['status'].upper()})",
        "",
        f"Generated {summary['generated_utc']}, covering the last 24 hours.",
        "",
    ]
    lines += ["## Warnings", ""] + ([f"- {w}" for w in summary["warnings"]] or ["- none"]) + [""]
    lines += [
        "## Feeds",
        "",
        f"- {f['polled_24h']} of {f['eligible']} feeds polled; last health check: "
        + ", ".join(f"{k} {v}" for k, v in sorted(f["status"].items())),
        *[f"- failing {x['fails']}x: {x['feed']} {x['name']} ({x['error']})" for x in f["failing_3plus"]],
        "",
        "## New stories",
        "",
        f"- {items['total']} new items: " + ", ".join(f"{k} {v}" for k, v in sorted(items["by_area"].items())),
        "- by source: " + ", ".join(f"{k} {v}" for k, v in sorted(items["by_source_type"].items())),
        f"- Kannada: {summary['kannada_24h']['translated']} of {summary['kannada_24h']['new']} translated",
        "",
        "## Google News (thin topics only)",
        "",
        *(
            [
                f"- {g['feed']}: last check {g['last_polled']}, {g['added_total']} stored so far"
                + (f", error {g['last_error']}" if g["last_error"] else "")
                for g in summary["google_news_thin_topics"]
            ]
            or ["- none configured"]
        ),
        "",
        "## Reddit",
        "",
        f"- {summary['reddit']['new_posts_24h']} new posts, {summary['reddit']['blocks_24h']} blocks"
        + (f", paused until {summary['reddit']['paused_until']}" if summary["reddit"]["paused_until"] else ""),
        "",
        "## Storage",
        "",
        f"- database {summary['storage']['db_mb']} MB, disk free {summary['storage']['disk_free_gb']} GB",
        f"- shortlist: {summary['shortlist'] or 'not written yet'}",
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(
            f"{summary['generated_utc']} DAILY {summary['status'].upper()} {items['total']} new items, "
            f"{f['polled_24h']}/{f['eligible']} feeds polled"
            + (f"; {'; '.join(summary['warnings'])}" if summary["warnings"] else "")
            + "\n"
        )
    return md
