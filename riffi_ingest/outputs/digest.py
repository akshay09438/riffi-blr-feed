"""The daily digest (BRIEF.md "Daily report" and step 8): reports/YYYY-MM-DD/digest.md and digest.csv.

Who it is for: the content team and the founder, once per run, by hand after a fetch (D-009, D-012). It answers
"what are today's best stories", "which topics moved" and "which High-priority topics went quiet".

A story the AI pass has checked (D-016) shows the AI's own "what's new" and debate angle. A story still waiting for
it carries keyword topics and a keyword-only score (up to 25 points lower), "what's new" reads "awaiting AI pass",
and its debate angle is the topic's general one from topics.csv, always marked "topic angle, not this story" so
nobody mistakes it for a take on the story itself (D-012).

Drop stories (excluded topics, or too far from Riffi's audience) are counted, never listed.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..db.connection import from_iso
from ..fetchers.parse import IST
from ..scoring import Scorer
from . import queries

CSV_CAP = 2000  # digest.csv lists at most this many stories (a day holds a few thousand at most)
ANGLE_MARK = "topic angle, not this story"
AWAITING = "awaiting AI pass"
MD_SOURCE_NAMES = 3  # names shown per story in digest.md; digest.csv has them all
MD_ANGLE_CHARS = 240
LABELS = ("High", "Medium", "Low", "Drop")
# link schemes (data: and file: only as links, so "Data: 3 lakh" is left alone), and domains: a name followed by a
# common top-level domain or by "/", or any all-lowercase name.ext (addresses are written that way). "B.Tech",
# "Mr.Siddaramaiah", "Node.js" and "Rs.500" are left alone.
SCHEME_RE = re.compile(r"(?i)\b(https?|ftp|mailto|javascript|vbscript|data(?=\s*:[^\s])|file(?=\s*:/))\s*:")
DOMAIN_RE = re.compile(
    r"(?i)\b([a-z0-9-]+)\.((?:com|in|org|net|io|co|gov|edu|info|me|ai|app|xyz|ly|us|uk|biz|site|online|link|to|cc)"
    r"\b|[a-z]{2,}(?=/))"
)
LOWER_DOMAIN_RE = re.compile(r"\b([a-z0-9-]+)\.([a-z]{2,})\b")
CSV_FIELDS = (
    "rank", "score", "label", "topics", "source_count", "sources", "headline", "url", "sensitive", "ai_checked",
    "whats_new", "debate_angle",
)  # fmt: skip


@dataclass
class Story:
    rank: int
    score: int | None
    label: str
    topics: list[str]
    source_count: int
    sources: list[str]
    headline: str
    url: str
    sensitive: bool
    ai_checked: bool
    angle_topic: str = ""
    angle: str = ""  # the best topic's general debate angles (topics.csv), never this story's own
    whats_new: str = ""  # the AI pass's, once it has checked the story
    ai_angle: str = ""  # the AI pass's debate angle for this story; shown instead of the topic's


@dataclass
class DigestResult:
    md_path: Path
    csv_path: Path
    counts: queries.Counts
    listed: int  # stories in digest.csv
    stories: list[Story] = field(default_factory=list)


def day_text(d: date) -> str:
    """6 Oct 2026"""
    return f"{d.day} {d:%b %Y}"


def at_text(when: datetime) -> str:
    """05 Oct 14:10 (IST)"""
    return when.astimezone(IST).strftime("%d %b %H:%M")


def cell(text: object) -> str:
    """Text safe inside a Markdown table cell: a `|` would end the cell, a line break the row."""
    return " ".join(str(text if text is not None else "").split()).replace("|", "\\|")


def plain(text: object) -> str:
    """A cell of text written by the AI pass: no link, web or email address, HTML or Markdown styling can come
    through it. Addresses are defanged ("https[:]//", "evil[.]example", "a[@]b") so they stay readable but no
    viewer turns them into links."""
    text = SCHEME_RE.sub(r"\1[:]", cell(text))
    while (defanged := LOWER_DOMAIN_RE.sub(r"\1[.]\2", DOMAIN_RE.sub(r"\1[.]\2", text))) != text:  # evil[.]co[.]in
        text = defanged
    text = text.replace("@", "[@]")
    for ch in ("[", "]", "`", "*", "_"):  # no link, code or emphasis markup
        text = text.replace(ch, "\\" + ch)
    return text.replace("<", "&lt;").replace(">", "&gt;")


def csv_safe(text: str) -> str:
    """Text from the web or the AI pass in a CSV cell: a leading = + - @ would make Excel run it as a formula."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _link(headline: str, url: str) -> str:
    text = cell(headline).replace("[", "\\[").replace("]", "\\]")
    if not url:
        return text
    safe = url.replace(" ", "%20").replace("|", "%7C").replace("(", "%28").replace(")", "%29")
    return f"[{text}]({safe})"


def _best_topic(topics: list[sqlite3.Row], scorer: Scorer) -> sqlite3.Row | None:
    """The topic the story scores by (Scorer.score: highest priority + geography points, first one on a tie)."""
    if not topics:
        return None
    return max(topics, key=lambda t: scorer.priority_points(t["priority"]) + scorer.geography_points(t["geography"]))


def _story(rank: int, row: sqlite3.Row, topics: list[sqlite3.Row], scorer: Scorer) -> Story:
    best = _best_topic(topics, scorer)
    score = row["relevance_score"]
    return Story(
        rank=rank,
        score=None if score is None else round(score),
        label=row["label"] or "",
        topics=[t["topic_id"] for t in topics],
        source_count=row["source_count"],
        sources=(row["source_names"] or "").split("; ") if row["source_names"] else [],
        headline=row["headline"],
        url=row["url"] or "",
        sensitive=bool(row["sensitive"]),
        ai_checked=bool(row["ai_checked"]),
        angle_topic=best["topic_id"] if best else "",
        angle=((best["debate_angles"] or "").strip() if best else ""),
        whats_new=row["ai_whats_new"] or "",
        ai_angle=row["ai_angle"] or "",
    )


def load_stories(conn: sqlite3.Connection, w: queries.Window, scorer: Scorer, limit: int | None = None) -> list[Story]:
    """The window's stories above Drop, best first, at most `limit` (default CSV_CAP)."""
    rows = queries.ranked_stories(conn, w, limit or CSV_CAP)
    topics = queries.story_topics(conn, [r["cluster_id"] for r in rows])
    return [_story(n, r, topics[r["cluster_id"]], scorer) for n, r in enumerate(rows, 1)]


def ai_points(scorer: Scorer) -> int:
    """The points only the AI pass can give (config/scoring.yaml), which keyword-only scores leave out."""
    return int(scorer.w.get("new_development", 0)) + int(scorer.w.get("debate_angle", 0))


def _cut(text: str, limit: int | None) -> str:
    return text if limit is None or len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _angle_text(s: Story, limit: int | None = None) -> str:
    """The AI's angle for this story once it has one; before the AI pass, the topic's general one, marked."""
    if s.ai_checked:
        return _cut(s.ai_angle, limit)
    if not s.angle:
        return ""
    return f"{ANGLE_MARK} ({s.angle_topic}): {_cut(s.angle, limit)}"


def _whats_new_text(s: Story) -> str:
    return s.whats_new if s.ai_checked else AWAITING


def _flags(s: Story) -> str:
    return ", ".join(f for f, on in (("sensitive", s.sensitive), ("awaiting AI", not s.ai_checked)) if on)


def _sources_text(s: Story) -> str:
    names = s.sources[:MD_SOURCE_NAMES]
    more = len(s.sources) - len(names)
    return f"{s.source_count}: " + "; ".join(names) + (f" +{more} more" if more > 0 else "")


def _head(w: queries.Window, c: queries.Counts, scorer: Scorer) -> list[str]:
    lines = [f"# Riffi digest, {day_text(w.day)} ({at_text(w.start)} to {at_text(w.end)} IST)", ""]
    if c.items == 0:
        return lines + [
            "No articles were fetched in this window, so there is nothing to rank."
            " Run a fetch first (python -m riffi_ingest fetch --all), then this command again.",
            "",
        ]
    if c.awaiting_ai:
        lines.append(
            f"AI pass: not run for {c.awaiting_ai:,} of {c.stories:,} stories. Their scores leave out up to"
            f" {ai_points(scorer)} points; what's new is {AWAITING}."
        )
    else:
        lines.append(f"AI pass: run for all {c.stories:,} stories.")
    labels = " / ".join(f"{c.labels.get(k, 0):,} {k}" for k in LABELS)
    unscored = f", {c.labels['Unscored']:,} not scored yet" if c.labels.get("Unscored") else ""
    lines += [
        "",
        f"Counts: {c.items:,} items fetched, {c.stories:,} stories ({c.new_stories:,} new), {labels}{unscored}.",
        "",
    ]
    return lines


def _top_section(stories: list[Story], top: int, c: queries.Counts) -> list[str]:
    listed = c.stories - c.labels.get("Drop", 0)
    lines = ["## Top stories", ""]
    if not stories:
        return lines + ["No stories above Drop in this window.", ""]
    shown = stories[:top]
    lines += [
        f"The best {len(shown)} of {listed:,} stories (Drop stories are counted above, never listed; every listed"
        " story is in digest.csv). What's new and the debate angle are the AI pass's; on a story it has not"
        f' checked yet, the angle is the topic\'s general one, marked "{ANGLE_MARK}".',
        "",
        "| # | Score | Label | Topics | Sources | Headline | What's new | Debate angle | Flags |",
        "|---:|---:|---|---|---|---|---|---|---|",
    ]
    for s in shown:
        score = "" if s.score is None else s.score
        lines.append(
            f"| {s.rank} | {score} | {s.label or '-'} | {cell(' '.join(s.topics)) or '-'} | {cell(_sources_text(s))}"
            f" | {_link(s.headline, s.url)} | {plain(_whats_new_text(s))} | {plain(_angle_text(s, MD_ANGLE_CHARS))}"
            f" | {_flags(s)} |"
        )
    return lines + [""]


def _moved_section(rows: list[sqlite3.Row]) -> list[str]:
    lines = ["## Topics with new developments", ""]
    if not rows:
        return lines + ["None in this window.", ""]
    for r in rows:
        n = r["stories"]
        lines.append(
            f"- **{r['topic_id']}** {cell(r['topic'] or '')} ({r['priority'] or 'no priority'}):"
            f" {n} {'story' if n == 1 else 'stories'}. Best: {cell(r['headline'])}"
        )
    return lines + [""]


def _quiet_section(rows: list[sqlite3.Row], end: datetime, history: datetime | None) -> list[str]:
    lines = ["## High-priority topics with nothing for 7+ days", ""]
    if history is None or end - history < queries.QUIET_AFTER:
        since = day_text(history.astimezone(IST).date()) if history else "today"
        lines += [
            f'History starts {since}, less than 7 days before this digest: here "nothing for 7+ days" means'
            " nothing since then.",
            "",
        ]
    if not rows:
        return lines + ["None: every High-priority topic had a story in the last 7 days.", ""]
    for r in rows:
        seen = from_iso(r["last_seen"])
        last = f"last story began {day_text(seen.astimezone(IST).date())}" if seen else "no story yet"
        lines.append(f"- **{r['topic_id']}** {cell(r['topic'])}: {last}")
    return lines + [""]


def markdown(
    w: queries.Window,
    c: queries.Counts,
    stories: list[Story],
    moved: list[sqlite3.Row],
    quiet: list[sqlite3.Row],
    history: datetime | None,
    *,
    top: int,
    scorer: Scorer,
) -> str:
    lines = _head(w, c, scorer)
    if c.items:
        lines += _top_section(stories, top, c) + _moved_section(moved)
    lines += _quiet_section(quiet, w.end, history)
    return "\n".join(lines).rstrip() + "\n"


def csv_row(s: Story) -> dict:
    return {
        "rank": s.rank,
        "score": "" if s.score is None else s.score,
        "label": s.label,
        "topics": " ".join(s.topics),
        "source_count": s.source_count,
        "sources": "; ".join(s.sources),
        "headline": csv_safe(s.headline),
        "url": s.url,
        "sensitive": "yes" if s.sensitive else "no",
        "ai_checked": "yes" if s.ai_checked else "no",
        "whats_new": csv_safe(_whats_new_text(s)),
        "debate_angle": csv_safe(_angle_text(s)),
    }


def write_digest(
    conn: sqlite3.Connection, w: queries.Window, folder: Path, *, top: int, scorer: Scorer
) -> DigestResult:
    """Write digest.md and digest.csv into `folder`, replacing any earlier copy (a rerun overwrites, D-012)."""
    c = queries.counts(conn, w)
    stories = load_stories(conn, w, scorer)
    text = markdown(
        w,
        c,
        stories,
        queries.topics_moved(conn, w),
        queries.quiet_high_topics(conn, w.end),
        queries.history_start(conn),
        top=top,
        scorer=scorer,
    )
    folder.mkdir(parents=True, exist_ok=True)
    md_path, csv_path = folder / "digest.md", folder / "digest.csv"
    md_path.write_text(text, encoding="utf-8")
    # utf-8-sig: Excel needs the BOM to show Kannada headlines
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(csv_row(s) for s in stories)
    return DigestResult(md_path, csv_path, c, len(stories), stories)
