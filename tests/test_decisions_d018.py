"""D-018 (28 Oct 2026, founder): public figures stay eligible for picks (their public actions and statements,
never their private life); private individuals stay excluded; the Uniform Civil Code is a tracked editor-only
subject (shown in picks.md, never in picks.json), not an exclusion."""

import csv

from riffi_ingest.filter import safety_state
from riffi_ingest.filter.model import StoryView
from riffi_ingest.pipeline import Paths
from riffi_ingest.tagging.keywords import KeywordTagger
from riffi_ingest.taxonomy import Taxonomy
from tests.conftest import REPO_ROOT


def sheet():
    return " ".join((REPO_ROOT / "config" / "prompts" / "ai_pass.md").read_text(encoding="utf-8").split())


def test_the_uniform_civil_code_is_an_editor_only_topic():
    with open(REPO_ROOT / "topics.csv", encoding="utf-8", newline="") as f:
        rows = {r["topic_id"]: r for r in csv.DictReader(f)}
    p58 = rows["P58"]
    assert p58["editor_only"] == "yes" and p58["category"] == "Politics" and "POL1" in p58["need_ids"]
    assert "REL2" in p58["need_ids"]  # marriage and family laws stays served


def test_ucc_stories_tag_p58_and_no_longer_tag_the_relationship_topic():
    tagger = KeywordTagger.load(REPO_ROOT / "config" / "topic_keywords.yaml")
    topics = tagger.tag("Assam cabinet clears Uniform Civil Code bill").topics
    assert "P58" in topics and "P26" not in topics
    assert "P58" in tagger.tag("UCC: what changes for marriage and inheritance").topics
    assert "P26" in tagger.tag("Dowry harassment cases and live-in registration rules").topics


def test_a_ucc_story_is_editor_only_not_excluded():
    p = Paths()
    tax = Taxonomy.load(p.categories, p.needs, p.topics)
    story = StoryView("c", "UCC bill", None, None, 1, [], [], ["P58"], ["P58"], True, False, "", "", "")
    assert safety_state.safety_state(story, tax.categories["Politics"], tax, gate0=True) == "editor_only"


def test_the_sheet_says_ucc_is_editor_only_and_not_a_flashpoint():
    text = sheet()
    table = text.split("## Editor-only subjects", 1)[1].split("## File 2", 1)[0]
    assert "Uniform Civil Code" in table and "P58" in table
    assert "The Uniform Civil Code is not a flashpoint by itself" in text


def test_the_sheet_separates_public_figures_from_private_individuals():
    text = sheet()
    assert "## Public figures and private individuals" in text
    block = text.split("## Public figures and private individuals", 1)[1].split("## ", 1)[0]
    for needle in (
        "public actions and statements",
        "Never write about their personal life",
        "private individual",
        "excluded",
    ):
        assert needle in block, needle
    assert "Politicians, celebrities, sportspeople and business leaders" in block
