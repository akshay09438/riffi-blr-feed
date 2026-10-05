"""The AI pass, through files (BRIEF.md step 4 part 2; D-005: Claude Code on the founder's plan, no API key;
D-016: run when the founder asks for the news of the last 24 hours).

1. `write_batches` picks the stories that wait for the AI: reports fetched in the window, tagged by the keyword
   pass or flagged unmatched-local, and either never checked or grown since their check (never one already
   excluded). Best first, at most `max_stories`, 20 per batch. It writes, into one new run folder,
   batch-NN.json (the stories as the AI reads them: headline, up to 5 reports, keyword topics, local flag) and
   topics.json (every topic the AI may answer with); and records each batch in the database (ai_batches: which
   stories, their report counts, when). The session writes into that folder, so nothing in it is trusted.
2. A Claude Code session follows config/prompts/ai_pass.md and writes answer-NN.json next to each batch.
3. `apply_answers` checks every answer against the database's record of its batch before trusting it
   (`check_answer`): exactly the expected fields, once each, of the right types; a story of that batch; topics
   that exist; short one-line text. A bad answer is rejected on its own and its story keeps waiting; a file for
   another batch, not JSON, or with over twice as many answers as its batch has stories is rejected whole. Each file's
   accepted answers and their stories' new scores are saved in one transaction, so a crash leaves no half.

The batch files carry text fetched from the open web. The instructions tell the session to treat it as data, and
the checks here are what make that hold: an answer can only set the verdict of a story in its own batch, and
an exclusion, once given, always stands (store.save_ai_verdict).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..db import store
from ..db.connection import utc_iso
from ..fetchers.parse import IST
from ..scoring import Scorer

INSTRUCTIONS = "config/prompts/ai_pass.md"
BATCH_SIZE = 20  # BRIEF.md: 20 stories per call
MAX_STORIES = 400  # per run: about 20 batches, a few minutes of the founder's plan
MAX_REPORTS = 5  # reports per story shown to the AI (titles differ; the rest add little)
SUMMARY_CHARS = 300
MAX_TEXT = 200  # what's new / debate angle / excluded reason: one line
MAX_TOPICS = 5
MAX_ANSWER_BYTES = 1024 * 1024
ANSWER_KEYS = (
    "story_id", "topic_ids", "is_new_development", "whats_new", "debate_angle", "excluded", "excluded_reason",
)  # fmt: skip
WS_RE = re.compile(r"\s+")
KEEP_FORMAT = {"\u200c", "\u200d"}  # the Kannada joiners: every other invisible format character is removed


class AnswerRejected(ValueError):
    pass


@dataclass(frozen=True)
class Verdict:
    story_id: str
    topic_ids: tuple[str, ...]
    is_new_development: bool
    whats_new: str
    debate_angle: str
    excluded: bool
    excluded_reason: str
    excluded_by_reason: bool = False  # excluded only because a reason was given with excluded: false


@dataclass
class BatchRun:
    folder: Path
    batches: list[Path] = field(default_factory=list)
    stories: int = 0
    left_out: int = 0  # waiting stories over the cap: sent next time


@dataclass
class ApplyResult:
    applied: int = 0
    excluded: int = 0
    older: int = 0  # answers skipped because a newer batch already checked the story
    unanswered: int = 0  # stories in an answered batch with no answer: still waiting
    rejected: list[tuple[str, str, str]] = field(default_factory=list)  # (file, story_id or "", reason)
    excluded_by_reason: list[tuple[str, str]] = field(default_factory=list)  # (story_id, reason): for a person
    files_missing: list[str] = field(default_factory=list)  # batches with no answer file yet


# ---- 1. writing the batches


def waiting_stories(conn: sqlite3.Connection, since: datetime, limit: int) -> list[sqlite3.Row]:
    """Stories with a report fetched since `since` that the AI should check (module docstring), best first."""
    return conn.execute(
        "WITH touched AS (SELECT DISTINCT cluster_id FROM items WHERE fetched_at >= ? AND cluster_id IS NOT NULL),"
        " counted AS (SELECT c.cluster_id, c.headline, c.unmatched_local, c.relevance_score,"
        "   (SELECT COUNT(*) FROM items i WHERE i.cluster_id = c.cluster_id) AS reports"
        "   FROM touched JOIN story_clusters c ON c.cluster_id = touched.cluster_id"
        "   WHERE c.unmatched_local = 1 OR EXISTS (SELECT 1 FROM item_topics it"
        "     WHERE it.cluster_id = c.cluster_id AND it.match_method = 'keyword'))"
        " SELECT counted.* FROM counted LEFT JOIN ai_verdicts v ON v.cluster_id = counted.cluster_id"
        " WHERE v.cluster_id IS NULL OR (v.excluded = 0 AND v.reports_seen < counted.reports)"
        " ORDER BY counted.relevance_score IS NULL, counted.relevance_score DESC, counted.cluster_id LIMIT ?",
        (utc_iso(since), limit),
    ).fetchall()


def _one_line(text: str | None, limit: int) -> str:
    text = WS_RE.sub(" ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _story_for_ai(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    reports, seen = [], set()
    for r in conn.execute(
        "SELECT i.title, i.summary, s.name FROM items i JOIN sources s ON s.source_id = i.source_id"
        " WHERE i.cluster_id = ? ORDER BY i.published_at, i.item_id",
        (row["cluster_id"],),
    ):
        key = (r["title"] or "").strip().lower()
        if key in seen:
            continue
        seen.add(key)
        reports.append(
            {
                "source": r["name"],
                "title": _one_line(r["title"], 300),
                "summary": _one_line(r["summary"], SUMMARY_CHARS),
            }
        )
        if len(reports) == MAX_REPORTS:
            break
    topics = conn.execute(
        "SELECT it.topic_id, t.topic FROM item_topics it LEFT JOIN topics t ON t.topic_id = it.topic_id"
        " WHERE it.cluster_id = ? AND it.match_method = 'keyword' ORDER BY it.topic_id",
        (row["cluster_id"],),
    )
    return {
        "story_id": row["cluster_id"],
        "headline": row["headline"],
        "local": bool(row["unmatched_local"]),
        "keyword_topics": [{"id": t["topic_id"], "topic": t["topic"] or ""} for t in topics],
        "reports": reports,
    }


def _topic_list(conn: sqlite3.Connection) -> list[dict]:
    return [
        {
            "id": r["topic_id"],
            "topic": r["topic"],
            "geography": r["geography"] or "",
            "priority": r["priority"] or "",
            "why_people_talk": _one_line(r["why_people_talk"], 300),
        }
        for r in conn.execute("SELECT * FROM topics ORDER BY topic_id")
    ]


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def write_batches(
    conn: sqlite3.Connection,
    out_dir: Path,
    *,
    since: datetime,
    now: datetime,
    max_stories: int = MAX_STORIES,
    size: int = BATCH_SIZE,
) -> BatchRun:
    """Write the waiting stories into a new folder under `out_dir`, named by the IST time (module docstring)."""
    rows = waiting_stories(conn, since, max_stories + 1)
    chosen = rows[:max_stories]
    stamp = now.astimezone(IST).strftime("%Y-%m-%d-%H%M%S")
    name, n = stamp, 1
    while (Path(out_dir) / name).exists() or _name_taken(conn, name):  # one folder per run, even in one second
        n += 1
        name = f"{stamp}-{n}"
    left_out = 0 if len(rows) <= max_stories else len(waiting_stories(conn, since, 10**9)) - max_stories
    run = BatchRun(Path(out_dir) / name, stories=len(chosen), left_out=left_out)
    if not chosen:
        return run
    run.folder.mkdir(parents=True)
    _write_json(run.folder / "topics.json", _topic_list(conn))
    written_at = now.astimezone(timezone.utc).isoformat(timespec="microseconds")
    records = []
    for n, start in enumerate(range(0, len(chosen), size), 1):
        part = chosen[start : start + size]
        batch_id = f"{name}-{n:02}"
        batch = {
            "batch_id": batch_id,
            "instructions": INSTRUCTIONS,
            "topics_file": "topics.json",
            "answer_file": f"answer-{n:02}.json",
            "stories": [_story_for_ai(conn, r) for r in part],
        }
        path = run.folder / f"batch-{n:02}.json"
        _write_json(path, batch)
        run.batches.append(path)
        seen = json.dumps({r["cluster_id"]: r["reports"] for r in part})
        records.append((batch_id, _folder_key(run.folder), path.name, batch["answer_file"], seen, written_at))
    with conn:
        conn.executemany(
            "INSERT INTO ai_batches (batch_id, folder, batch_file, answer_file, stories, written_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            records,
        )
    return run


def _name_taken(conn: sqlite3.Connection, name: str) -> bool:
    """Batch ids are <folder name>-NN, so a name already used anywhere cannot be used again."""
    return conn.execute("SELECT EXISTS (SELECT 1 FROM ai_batches WHERE batch_id = ?)", (f"{name}-01",)).fetchone()[0]


def _folder_key(folder: Path) -> str:
    """The folder's full path, as recorded and compared (case-folded on Windows)."""
    return os.path.normcase(str(Path(folder).resolve()))


# ---- 3. checking and applying the answers


def _text(a: dict, key: str) -> str:
    value = a[key]
    if not isinstance(value, str):
        raise AnswerRejected(f"{key} must be text")
    # control characters, and format characters that reorder or hide text (direction marks, tags, soft hyphens)
    value = "".join(
        ch
        for ch in value
        if (unicodedata.category(ch) != "Cc" or ch.isspace())
        and (unicodedata.category(ch) != "Cf" or ch in KEEP_FORMAT)
    )
    value = WS_RE.sub(" ", value).strip()
    if len(value) > MAX_TEXT:
        raise AnswerRejected(f"{key} is too long ({len(value)} characters, at most {MAX_TEXT})")
    return value


def _flag(a: dict, key: str) -> bool:
    if type(a[key]) is not bool:  # "true", 1 and null are not answers
        raise AnswerRejected(f"{key} must be true or false")
    return a[key]


def check_answer(a, allowed: set[str], topic_ids: set[str]) -> Verdict:
    """One answer, checked field by field; raises AnswerRejected with the reason."""
    if not isinstance(a, dict):
        raise AnswerRejected("the answer is not an object")
    missing = [k for k in ANSWER_KEYS if k not in a]
    if missing:
        raise AnswerRejected(f"missing {', '.join(missing)}")
    extra = sorted(set(a) - set(ANSWER_KEYS))
    if extra:
        raise AnswerRejected(f"unexpected {', '.join(map(str, extra))}")
    sid = a["story_id"]
    if not isinstance(sid, str) or sid not in allowed:
        raise AnswerRejected("the story is not in this batch")
    tids = a["topic_ids"]
    if not isinstance(tids, list) or len(tids) > MAX_TOPICS or not all(isinstance(t, str) for t in tids):
        raise AnswerRejected(f"topic_ids must be a list of at most {MAX_TOPICS} topic ids")
    unknown = sorted({t for t in tids if t not in topic_ids})
    if unknown:
        raise AnswerRejected(f"unknown topic {', '.join(unknown)}")
    new, excluded = _flag(a, "is_new_development"), _flag(a, "excluded")
    whats_new, angle, reason = _text(a, "whats_new"), _text(a, "debate_angle"), _text(a, "excluded_reason")
    if new and not whats_new:
        raise AnswerRejected("whats_new is empty for a new development")
    if excluded and not reason:
        raise AnswerRejected("excluded_reason is empty for an excluded story")
    by_reason = not excluded and bool(reason)  # a reason to exclude is an exclusion: when in doubt, exclude
    return Verdict(sid, tuple(dict.fromkeys(tids)), new, whats_new, angle, excluded or by_reason, reason, by_reason)


def _no_repeats(pairs: list[tuple]) -> dict:
    keys = Counter(k for k, _ in pairs)
    twice = sorted(k for k, n in keys.items() if n > 1)
    if twice:
        raise AnswerRejected(f"a field is given twice in one object: {', '.join(twice)}")
    return dict(pairs)


def _read_answers(path: Path, batch_id: str, stories: int) -> list:
    if path.stat().st_size > MAX_ANSWER_BYTES:
        raise AnswerRejected("the file is too large")
    try:
        # utf-8-sig: Windows PowerShell 5.1 writes "utf8" files with a byte-order mark
        data = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=_no_repeats)
    except AnswerRejected:
        raise
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise AnswerRejected(f"not valid JSON ({type(exc).__name__}: {str(exc)[:200]})") from exc
    if not isinstance(data, dict) or set(data) != {"batch_id", "answers"} or not isinstance(data["answers"], list):
        raise AnswerRejected('the file must be {"batch_id": ..., "answers": [...]}')
    if data["batch_id"] != batch_id:
        raise AnswerRejected(f"batch_id is {str(data['batch_id'])[:80]!r}, expected {batch_id!r}")
    if len(data["answers"]) > 2 * stories:  # a stray answer is rejected on its own; this many is not a mistake
        raise AnswerRejected(f"far more answers ({len(data['answers'])}) than the batch has stories ({stories})")
    return data["answers"]


def batches_in(conn: sqlite3.Connection, folder: Path) -> list[sqlite3.Row]:
    """The engine's record of the batches written into `folder` (that very folder, by its full path), in order."""
    return conn.execute(
        "SELECT * FROM ai_batches WHERE folder = ? ORDER BY batch_file", (_folder_key(folder),)
    ).fetchall()


def _apply_file(
    conn, path: Path, entry: sqlite3.Row, topic_ids: set[str], scorer: Scorer, now: datetime, result: ApplyResult
) -> None:
    """One answer file: check it, then save its accepted answers and their stories' scores in one transaction.
    Raises AnswerRejected for a file rejected whole (nothing of it is saved)."""
    seen = json.loads(entry["stories"])
    answers = _read_answers(path, entry["batch_id"], len(seen))
    ids = Counter(a.get("story_id") for a in answers if isinstance(a, dict) and isinstance(a.get("story_id"), str))
    good: list[Verdict] = []
    rejected: list[tuple[str, str, str]] = []
    for a in answers:
        sid = a.get("story_id", "") if isinstance(a, dict) else ""
        sid = sid if isinstance(sid, str) else ""
        if ids[sid] > 1:
            rejected.append((path.name, sid, "answered twice in one file"))
            continue
        try:
            good.append(check_answer(a, set(seen), topic_ids))
        except AnswerRejected as exc:
            rejected.append((path.name, sid, str(exc)))
    counts, by_reason = Counter(), []
    with conn:  # all or nothing for this file: verdicts and the scores they lead to
        for v in good:
            outcome = store.save_ai_verdict(
                conn, v, reports_seen=seen[v.story_id], batch_id=entry["batch_id"],
                batch_written_at=entry["written_at"], now=now,
            )  # fmt: skip
            counts[outcome] += 1
            if outcome != "older":
                store.write_score(conn, v.story_id, scorer.score(store.story_facts(conn, v.story_id)))
            if outcome == "saved" and v.excluded or outcome == "older-excluded":
                counts["excluded"] += 1
                if v.excluded_by_reason:
                    by_reason.append((v.story_id, v.excluded_reason))
    result.applied += counts["saved"]
    result.older += counts["older"] + counts["older-excluded"]
    result.excluded += counts["excluded"]
    result.excluded_by_reason += by_reason
    result.rejected += rejected
    answered = {v.story_id for v in good} | {sid for _, sid, _ in rejected}
    result.unanswered += sum(1 for sid in seen if sid not in answered)


def apply_answers(conn: sqlite3.Connection, folder: Path, scorer: Scorer, *, now: datetime) -> ApplyResult:
    """Check and store every answer file in a run folder that ai-batch wrote; each file is saved whole or not at
    all. Safe to repeat. Raises ValueError for a folder the engine has no record of."""
    folder = Path(folder)
    entries = batches_in(conn, folder)
    if not entries:
        raise ValueError(f"{folder} is not a folder written by ai-batch (the database has no record of it)")
    topic_ids = {r[0] for r in conn.execute("SELECT topic_id FROM topics")}
    result = ApplyResult()
    for entry in entries:
        path = folder / entry["answer_file"]
        if not path.exists():
            result.files_missing.append(entry["batch_file"])
            continue
        try:
            _apply_file(conn, path, entry, topic_ids, scorer, now, result)
        except AnswerRejected as exc:
            result.rejected.append((path.name, "", str(exc)))
        except Exception as exc:  # one unreadable file must not stop the others; its transaction rolled back
            result.rejected.append((path.name, "", f"could not be applied ({type(exc).__name__}: {str(exc)[:200]})"))
    return result
