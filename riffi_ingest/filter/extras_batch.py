"""`extras-batch`: batches of the stories the Daily Picks need the AI pass's extra judgements for (why the reader
cares, the two sides, a hook, a format), chosen like the AI-pass prefilter does (`prefilter.choose_for_ai`): the
best stories of every category, Bangalore High first, up to the budget. `ai-batch` only sends stories the AI has
not judged yet; this sends the best stories whether or not they were judged, so the extras never depend on it.

Each batch is the same shape as an `ai-batch` batch, with `"kind": "extras"` and an `extra_file` instead of an
`answer_file`: the session writes only `extra-NN.json` for it. Nothing here is a verdict: no topic, score or
exclusion can change from these files. The allow-list of which stories each batch may name is a record outside
the batch folder (the session writes into the folder), and `ai-facts` accepts the folder only with that record.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..fetchers.parse import IST
from ..outputs import queries
from ..tagging import llm_batches
from ..taxonomy import Taxonomy
from .assess import assess
from .config import FilterConfig
from .facts import load_facts
from .model import Assessed, load_stories
from .prefilter import choose_for_ai, rank_key

BATCH_SIZE = llm_batches.BATCH_SIZE


@dataclass
class ExtrasRun:
    folder: Path | None
    stories: int = 0
    batches: list[Path] = field(default_factory=list)
    per_category: dict[str, int] = field(default_factory=dict)
    skipped_done: int = 0  # already have extras today
    waiting_for_answers: int = 0  # chosen stories that still need the ordinary AI answer (ai-batch)
    left_out: int = 0


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _payload(conn: sqlite3.Connection, a: Assessed, taxonomy: Taxonomy) -> dict:
    s = a.story
    base = llm_batches._story_for_ai(conn, {"cluster_id": s.story_id, "headline": s.headline, "unmatched_local": 0})
    editor = taxonomy.editor_only_topics()
    base.update(
        {
            "category": a.category,
            "topics": [
                {"id": t, "topic": taxonomy.topics[t].title, "editor_only": t in editor}
                for t in s.topic_ids
                if t in taxonomy.topics
            ],
            "editor_only": bool(set(s.all_topic_ids) & editor),
            "ai_judged": s.ai_checked,
            "ai_whats_new": s.whats_new if s.ai_checked else "",
            "ai_debate_angle": s.ai_angle if s.ai_checked else "",
        }
    )
    return base


def write_extras_batches(
    conn: sqlite3.Connection,
    w: queries.Window,
    taxonomy: Taxonomy,
    cfg: FilterConfig,
    out_dir: Path,
    records_dir: Path,
    *,
    now: datetime,
    sidecar: Path | None = None,
    max_stories: int | None = None,
    again: bool = False,
    size: int = BATCH_SIZE,
) -> ExtrasRun:
    stories = load_stories(conn, w, int(cfg["picks"]["max_links"]))
    done = set(load_facts(sidecar)[0]) if sidecar is not None and not again else set()
    assessment = assess(stories, {}, taxonomy, cfg, now=now, gate0=False)
    fresh = [a for a in assessment.assessed if a.fresh]  # a story older than the freshness limit is never picked
    todo = [a for a in fresh if a.story.story_id not in done]
    budget = int(max_stories) if max_stories is not None else int(cfg["prefilter"]["max_stories"])
    sized = FilterConfig({**cfg.raw, "prefilter": {**cfg["prefilter"], "max_stories": budget}})
    pre = choose_for_ai(todo, taxonomy, sized, now)
    chosen = sorted(pre.chosen, key=lambda a: rank_key(a, cfg, now))  # best first: the first batches matter most
    run = ExtrasRun(
        None,
        stories=len(chosen),
        per_category=dict(pre.per_category),
        skipped_done=len(fresh) - len(todo),
        waiting_for_answers=sum(not a.story.ai_checked for a in chosen),
        left_out=pre.left_out,
    )
    if not chosen:
        return run
    stamp = now.astimezone(IST).strftime("%Y-%m-%d-%H%M%S")
    name, n = f"{stamp}-extras", 1
    while (Path(out_dir) / name).exists() or (Path(records_dir) / f"{name}.json").exists():
        n += 1
        name = f"{stamp}-extras-{n}"
    folder = Path(out_dir) / name
    folder.mkdir(parents=True)
    run.folder = folder
    _write_json(folder / "topics.json", llm_batches._topic_list(conn))
    records = []
    for i, start in enumerate(range(0, len(chosen), size), 1):
        part = chosen[start : start + size]
        batch_id = f"{name}-{i:02}"
        batch = {
            "batch_id": batch_id,
            "kind": "extras",
            "instructions": llm_batches.INSTRUCTIONS,
            "topics_file": "topics.json",
            "extra_file": f"extra-{i:02}.json",
            "stories": [_payload(conn, a, taxonomy) for a in part],
        }
        path = folder / f"batch-{i:02}.json"
        _write_json(path, batch)
        run.batches.append(path)
        records.append(
            {
                "batch_id": batch_id,
                "batch_file": path.name,
                "extra_file": batch["extra_file"],
                "stories": [a.story.story_id for a in part],
            }
        )
    Path(records_dir).mkdir(parents=True, exist_ok=True)
    _write_json(
        Path(records_dir) / f"{name}.json",
        {
            "folder": str(folder.resolve()),
            "written_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "batches": records,
        },
    )
    return run
