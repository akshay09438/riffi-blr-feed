"""Where a story stands on safety for the picks (D-017, PRD Gate 0). This is the part of Gate 0 that needs no
protected file; the word-list check itself is proposal 4c.

excluded     a communal or religious flashpoint (the AI verdict, sticky) - never assessed, in no export
editor_only  tracked and shown to the team, never exported to the seed agent: one of its topics is marked
             editor_only in topics.csv, or its category is editor-only until the safety filter exists
pending      nobody has finished checking it: the AI pass has not judged it (or it grew since), or the
             safety filter (Gate 0 word lists) is not built yet
cleared      the only state that can reach picks.json

Fail-closed: `cleared` needs the safety filter module to exist (`riffi_ingest.safety.lexicon`, proposal 4c),
not a setting someone could switch on by mistake.
"""

from __future__ import annotations

import importlib.util

from ..taxonomy import CategoryRule, Taxonomy
from .model import StoryView

CLEARED, EDITOR_ONLY, PENDING = "cleared", "editor_only", "pending"


def gate0_shipped() -> bool:
    """True once proposal 4c has put the safety filter in the code base."""
    try:
        return importlib.util.find_spec("riffi_ingest.safety.lexicon") is not None
    except (ImportError, ValueError):
        return False


def safety_state(story: StoryView, category: CategoryRule | None, taxonomy: Taxonomy, *, gate0: bool) -> str:
    """For a story that is not excluded."""
    editor_topics = taxonomy.editor_only_topics()
    if any(t in editor_topics for t in story.topic_ids):
        return EDITOR_ONLY
    if category is not None and category.editor_only_until_safety and not gate0:
        return EDITOR_ONLY
    if not story.ai_checked or not gate0:
        return PENDING
    return CLEARED
