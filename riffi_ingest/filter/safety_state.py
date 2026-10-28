"""Where a story stands on safety for the picks (D-017, PRD Gate 0). This is the part of Gate 0 that needs no
protected file; the word-list check itself is proposal 4c.

excluded     a communal or religious flashpoint (the AI verdict, sticky) - never assessed, in no export
editor_only  tracked and shown to the team, never exported to the seed agent: one of its topics is marked
             editor_only in topics.csv, or its category is editor-only until the safety filter exists
pending      nobody has finished checking it: the AI pass has not judged it (or it grew since), or the
             safety filter (Gate 0 word lists) is not built yet
cleared      the only state that can reach picks.json

Fail-closed: `cleared` needs the safety filter (`riffi_ingest.safety.lexicon`, proposal 4c) to exist AND to say
it is ready: the module must define a function `gate0_ready()` that returns exactly True (proposal 4c: only
when config/exclusions.yaml is enabled and loads). A missing module, an empty file, a missing function, a
function that raises, or any other answer all mean "not shipped".
"""

from __future__ import annotations

import importlib

from ..taxonomy import CategoryRule, Taxonomy
from .model import StoryView

CLEARED, EDITOR_ONLY, PENDING = "cleared", "editor_only", "pending"


def gate0_shipped() -> bool:
    """True only when proposal 4c's safety filter exists and reports itself ready."""
    try:
        module = importlib.import_module("riffi_ingest.safety.lexicon")
        ready = getattr(module, "gate0_ready", None)
        return callable(ready) and ready() is True
    except Exception:  # a broken safety module must close the gate, never open it
        return False


def safety_state(story: StoryView, category: CategoryRule | None, taxonomy: Taxonomy, *, gate0: bool) -> str:
    """For a story that is not excluded."""
    editor_topics = taxonomy.editor_only_topics()
    # the keyword pass's tags count even when the AI's tags replaced them for scoring: a leader or party topic
    # the keyword pass saw must not vanish because the AI chose a generic topic
    if any(t in editor_topics for t in (*story.topic_ids, *story.all_topic_ids)):
        return EDITOR_ONLY
    if category is not None and category.editor_only_until_safety and not gate0:
        return EDITOR_ONLY
    if not story.ai_checked or not gate0:
        return PENDING
    return CLEARED
