"""`ai-facts`: the AI pass's extra judgements (PRD section 8.6) go into the sidecar file the picks read.

The AI session writes, next to each `answer-NN.json`, an `extra-NN.json`:

    {"batch_id": "<copied from the batch>", "extras": [ {"story_id": "...", "why_care": "...", ...}, ... ]}

This keeps the seven-field answer format and its checker (a protected file) exactly as they are. The checks
here are the same in spirit: the folder must be one the engine wrote (the database's own record decides which
stories a batch may touch), a story the AI pass excluded gets no extras, every number is clamped and every
text cut to one plain line (`facts.clean`), and the file is size-limited. The result is merged into
`<out>/<date>/story_facts_v2.json`, a later judgement replacing an earlier one for the same story.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..tagging import llm_batches
from .facts import MAX_FILE_BYTES, clean, load_facts

SIDECAR = "story_facts_v2.json"
MAX_EXTRA_BYTES = 1024 * 1024


@dataclass
class ExtraResult:
    applied: int = 0
    rejected: list[tuple[str, str, str]] = field(default_factory=list)  # (file, story id or '', reason)
    files_missing: list[str] = field(default_factory=list)
    skipped_excluded: int = 0


def extra_name(answer_file: str) -> str:
    """answer-03.json -> extra-03.json"""
    return "extra-" + answer_file.removeprefix("answer-")


def _read(path: Path, batch_id: str, stories: int) -> list:
    if path.stat().st_size > MAX_EXTRA_BYTES:
        raise ValueError("file is over 1 MB")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, RecursionError, UnicodeDecodeError) as exc:
        raise ValueError(f"not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or set(data) != {"batch_id", "extras"}:
        raise ValueError('must be exactly {"batch_id": ..., "extras": [...]}')
    if data["batch_id"] != batch_id:
        raise ValueError(f"batch_id is {data['batch_id']!r}, expected {batch_id!r}")
    if not isinstance(data["extras"], list) or len(data["extras"]) > 2 * stories:
        raise ValueError("extras must be a list with at most twice as many entries as the batch has stories")
    return data["extras"]


def apply_extras(conn: sqlite3.Connection, folder: Path, sidecar: Path) -> ExtraResult:
    result = ExtraResult()
    batches = llm_batches.batches_in(conn, folder)
    if not batches:
        raise LookupError(f"{folder} is not a folder written by ai-batch for this database")
    excluded = {r[0] for r in conn.execute("SELECT cluster_id FROM ai_verdicts WHERE excluded = 1")}
    merged = {sid: asdict(f) for sid, f in load_facts(sidecar)[0].items()}
    for entry in batches:
        name = extra_name(entry["answer_file"])
        path = Path(folder) / name
        if not path.is_file():
            result.files_missing.append(name)
            continue
        seen = set(json.loads(entry["stories"]))
        try:
            extras = _read(path, entry["batch_id"], len(seen))
        except ValueError as exc:
            result.rejected.append((name, "", f"file rejected: {exc}"))
            continue
        ids = [e.get("story_id") for e in extras if isinstance(e, dict)]
        for e in extras:
            sid = e.get("story_id") if isinstance(e, dict) else None
            if not isinstance(sid, str) or sid not in seen:
                result.rejected.append((name, str(sid or ""), "not a story of this batch"))
            elif ids.count(sid) > 1:
                result.rejected.append((name, sid, "given twice in one file"))
            elif sid in excluded:
                result.skipped_excluded += 1
            else:
                merged[sid] = asdict(clean(e))
                result.applied += 1
    if result.applied:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        tmp = sidecar.with_suffix(".tmp")
        text = json.dumps(merged, ensure_ascii=False, indent=1) + "\n"
        if len(text.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("the merged sidecar would be over the size limit")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, sidecar)
    return result
