"""The Daily Picks (D-017, PRD section 6.4): picks.md for the content team, picks.csv, picks.json for the seed
agent (cleared picks only), candidates.csv for audit and filter_report.md for the founder, all in
<out>/YYYY-MM-DD/. A rerun overwrites, like the digest.

Text that came from the web or the AI pass goes through the digest's `plain` / `csv_safe`, so no link, HTML or
spreadsheet formula can ride in on a headline or a judgement."""

from __future__ import annotations

import csv
import json
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..fetchers.parse import IST
from ..filter.assess import Assessment, assess
from ..filter.config import FilterConfig
from ..filter.facts import load_facts
from ..filter.model import Assessed, load_stories
from ..filter.prefilter import Prefilter, choose_for_ai
from ..filter.safety_state import CLEARED, gate0_shipped
from ..filter.select import Selection, select
from ..taxonomy import Taxonomy
from . import queries
from .digest import _link, cell, csv_safe, day_text, plain

PICK_FIELDS = (
    "run_id", "rank", "category", "need_id", "topic_id", "story_id", "headline", "why_care", "side_a", "side_b",
    "hook", "format", "R", "K", "P", "tier", "safety_state", "ai_missing", "sources", "links", "first_seen",
)  # fmt: skip
CANDIDATE_FIELDS = (
    "story_id", "headline", "category", "need_id", "topic_id", "safety_state", "R", "K", "P", "gate1_pass",
    "gate2_pass", "ai_missing", "picked", "reason", "sources", "first_seen",
)  # fmt: skip
SIDECAR = "story_facts_v2.json"


@dataclass
class PicksRun:
    run_id: str
    window: queries.Window
    assessment: Assessment
    selection: Selection
    prefilter: Prefilter
    gate0: bool
    problems: list[str]
    seconds: float
    awaiting_ai: int
    files: dict[str, Path] = field(default_factory=dict)


def run_id_for(now: datetime) -> str:
    return now.astimezone(IST).strftime("%Y%m%d-%H%M")


def build(
    conn: sqlite3.Connection,
    w: queries.Window,
    taxonomy: Taxonomy,
    cfg: FilterConfig,
    folder: Path,
    *,
    now: datetime,
    gate0: bool | None = None,
) -> PicksRun:
    started = time.monotonic()
    gate = gate0_shipped() if gate0 is None else gate0
    stories = load_stories(conn, w, int(cfg["picks"]["max_links"]))
    facts, problems = load_facts(folder / SIDECAR)
    assessment = assess(stories, facts, taxonomy, cfg, now=now, gate0=gate)
    selection = select(assessment.assessed, taxonomy, cfg)
    pre = choose_for_ai(assessment.assessed, taxonomy, cfg)
    awaiting = sum(1 for s in stories if not s.excluded and not s.ai_checked and s.topic_ids)
    return PicksRun(
        run_id_for(now), w, assessment, selection, pre, gate, problems, time.monotonic() - started, awaiting
    )


def _ist(when: datetime) -> str:
    return when.astimezone(IST).strftime("%d %b %H:%M")


def pick_row(run_id: str, rank: int, a: Assessed) -> dict:
    f = a.story.facts
    return {
        "run_id": run_id,
        "rank": rank,
        "category": a.category,
        "need_id": a.need_id,
        "topic_id": a.topic.topic_id,
        "story_id": a.story.story_id,
        "headline": a.story.headline,
        "why_care": (f.why_care if f and f.why_care else a.story.whats_new),
        "side_a": f.side_a if f else "",
        "side_b": f.side_b if f else "",
        "hook": f.hook if f else "",
        "format": f.format if f else "",
        "R": a.r,
        "K": a.k,
        "P": a.p,
        "tier": a.tier,
        "safety_state": a.safety_state,
        "ai_missing": "yes" if a.ai_missing else "no",
        "sources": "; ".join(a.story.sources),
        "links": " ".join(a.story.links),
        "first_seen": a.story.started_at.astimezone(IST).isoformat(timespec="minutes"),
    }


def _pick_block(rank: int, a: Assessed) -> list[str]:
    row = pick_row("", rank, a)
    head = f"**[{a.category} · {a.need_id} · {a.tier} · P {a.p:g}]** {_link(a.story.headline, a.story.links[0] if a.story.links else '')}"
    lines = [head, ""]
    why = (
        plain(row["why_care"])
        if row["why_care"]
        else "*(no AI judgement yet: run the AI pass for the reason and the two sides)*"
    )
    lines.append(f"- Why you care: {why}")
    if row["side_a"] or row["side_b"]:
        lines.append(f"- Side A: {plain(row['side_a']) or '-'}  ·  Side B: {plain(row['side_b']) or '-'}")
    if row["format"] or row["hook"]:
        idea = " — open on: " + plain(row["hook"]) if row["hook"] else ""
        lines.append(f"- Content idea: {plain(row['format']) or 'idea pending'}{idea}")
    lines.append(f"- Riffi topic: {plain(a.topic.title or a.topic.topic_id)} ({a.topic.topic_id})")
    names = a.story.sources
    shown = "; ".join(plain(n) for n in names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")
    lines.append(f"- Sources: {shown} · first seen {_ist(a.story.started_at)} IST · R {a.r}, K {a.k}")
    flags = []
    if a.safety_state != CLEARED:
        flags.append(f"safety: {a.safety_state}")
    if a.ai_missing:
        flags.append("ai_missing")
    if flags:
        lines.append(f"- Flags: {', '.join(flags)}")
    for url in a.story.links[1:]:
        lines.append(f"- More: {_link('link', url)}")
    return lines + [""]


def picks_md(run: PicksRun, taxonomy: Taxonomy) -> str:
    day = day_text(run.window.day)
    picks = run.selection.picks
    lines = [
        f"# Daily Picks: {day}",
        "",
        f"Run {run.run_id} (IST). {len(picks)} picks from {run.assessment.total} stories.",
        "",
    ]
    if not run.gate0:
        lines += [
            "> **Safety:** the safety word-list filter is not built yet, so nothing here is cleared for the seed agent and"
            " `picks.json` is empty. Every pick is for the editor, who checks the framing before anything is posted.",
            "",
        ]
    lines += [f"> {cell(n)}" for n in run.selection.notes] + ([""] if run.selection.notes else [])
    by_cat: dict[str, list[Assessed]] = {}
    for a in picks:
        by_cat.setdefault(a.category, []).append(a)
    rank = {a.story.story_id: i for i, a in enumerate(picks, 1)}
    for name in taxonomy.categories:
        group = by_cat.get(name)
        if not group:
            continue
        lines += [f"## {name} ({len(group)})", ""]
        for a in group:
            lines += _pick_block(rank[a.story.story_id], a)
    if not picks:
        lines += ["No story passed every gate in this window. See `candidates.csv` and `filter_report.md`.", ""]
    return "\n".join(lines).rstrip() + "\n"


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # the BOM lets Excel show Kannada
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: (csv_safe(v) if isinstance(v, str) else v) for k, v in r.items()})


def candidate_row(a: Assessed) -> dict:
    return {
        "story_id": a.story.story_id,
        "headline": a.story.headline,
        "category": a.category,
        "need_id": a.need_id,
        "topic_id": a.topic.topic_id,
        "safety_state": a.safety_state,
        "R": a.r,
        "K": a.k,
        "P": a.p,
        "gate1_pass": "yes" if a.gate1 else "no",
        "gate2_pass": "yes" if a.gate2 else "no",
        "ai_missing": "yes" if a.ai_missing else "no",
        "picked": "yes" if a.picked else "no",
        "reason": a.reason,
        "sources": "; ".join(a.story.sources),
        "first_seen": a.story.started_at.astimezone(IST).isoformat(timespec="minutes"),
    }


def filter_report_md(run: PicksRun, taxonomy: Taxonomy, cfg: FilterConfig) -> str:
    a = run.assessment
    sel = run.selection
    lines = [f"# Filter report: {day_text(run.window.day)} (run {run.run_id})", ""]
    lines += [
        f"- Stories in the window: {a.total}. Excluded as communal or religious flashpoints: {sum(a.excluded.values())}."
        f" No tracked topic: {a.no_topic}. Reached the gates: {len(a.assessed)}.",
        f"- Passed relevance (Gate 1): {sum(x.gate1 for x in a.assessed)}. Passed relevance and content-ability:"
        f" {sum(x.gate1 and x.gate2 for x in a.assessed)}. Picked: {len(sel.picks)} (target {cfg['picks']['target']}).",
        "- Safety states of the picks: "
        + (", ".join(f"{n} {k}" for k, n in sorted(Counter(p.safety_state for p in sel.picks).items())) or "none")
        + ".",
        f"- Safety word-list filter built: {'yes' if run.gate0 else 'NO (nothing can be cleared for the seed agent)'}.",
        f"- AI pass: {run.awaiting_ai} tagged stories still wait for the AI pass;"
        f" {sum(not x.ai_missing for x in a.assessed)} of {len(a.assessed)} candidates have the AI's extra judgements"
        f" (`{SIDECAR}`); the others are scored from signals only and marked ai_missing.",
        f"- Filter run time: {run.seconds:.1f} s.",
        "",
    ]
    lines += [f"> {cell(n)}" for n in sel.notes] + ([""] if sel.notes else [])
    lines += [
        "## Per category",
        "",
        "| Category | Candidates | Passed R | Passed R and K | Picked |",
        "|---|---|---|---|---|",
    ]
    for name in taxonomy.categories:
        grp = [x for x in a.assessed if x.category == name]
        lines.append(
            f"| {name} | {len(grp)} | {sum(x.gate1 for x in grp)} | {sum(x.gate1 and x.gate2 for x in grp)} | {sum(x.picked for x in grp)} |"
        )
    lines += ["", "## Top exclusion reasons (counts only)", ""]
    top = a.excluded.most_common(10)
    lines += [f"- {cell(reason)}: {n}" for reason, n in top] or ["None."]
    reasons = Counter(x.reason for x in a.assessed if not x.picked and x.reason)
    lines += ["", "## Why candidates were not picked", ""]
    lines += [f"- {cell(r)}: {n}" for r, n in reasons.most_common(8)] or ["None."]
    chosen_missing = [x for x in run.prefilter.chosen if not x.story.ai_checked]
    lines += [
        "",
        "## AI pass coverage",
        "",
        f"The prefilter would send {len(run.prefilter.chosen)} stories to the AI pass ({run.prefilter.left_out} left out);"
        f" {len(chosen_missing)} of them have no current AI verdict.",
    ]
    if run.problems:
        lines += ["", "## Problems with the sidecar file", ""] + [f"- {cell(p)}" for p in run.problems[:10]]
    return "\n".join(lines).rstrip() + "\n"


def write_picks(run: PicksRun, folder: Path, taxonomy: Taxonomy, cfg: FilterConfig) -> PicksRun:
    folder.mkdir(parents=True, exist_ok=True)
    picks = run.selection.picks
    rows = [pick_row(run.run_id, i, a) for i, a in enumerate(picks, 1)]
    run.files = {
        "picks.md": folder / "picks.md",
        "picks.csv": folder / "picks.csv",
        "picks.json": folder / "picks.json",
        "candidates.csv": folder / "candidates.csv",
        "filter_report.md": folder / "filter_report.md",
    }
    run.files["picks.md"].write_text(picks_md(run, taxonomy), encoding="utf-8")
    _write_csv(run.files["picks.csv"], PICK_FIELDS, rows)
    exported = [r for r, a in zip(rows, picks, strict=True) if a.safety_state == CLEARED]
    payload = {"run_id": run.run_id, "generated_for": run.window.day.isoformat(), "picks": exported}
    run.files["picks.json"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ordered = sorted(run.assessment.assessed, key=lambda x: (-x.p, x.story.story_id))
    _write_csv(run.files["candidates.csv"], CANDIDATE_FIELDS, [candidate_row(x) for x in ordered])
    run.files["filter_report.md"].write_text(filter_report_md(run, taxonomy, cfg), encoding="utf-8")
    return run
