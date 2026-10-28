"""Category -> need -> topic (riffi_ingest/taxonomy.py, D-017)."""

import textwrap

from riffi_ingest.taxonomy import Taxonomy, needs_in_notes, split_ids


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
    return path


def small(tmp_path, topics, feeds=None):
    cats = write(
        tmp_path,
        "categories.yaml",
        """
        defaults: {quota_min: 1, quota_max: 4, prefilter_cap: 30, editor_only_until_safety: false}
        categories:
          Bangalore: {quota_min: 4, quota_max: 4, prefilter_cap: 60}
          Politics: {quota_min: 0, quota_max: 3, editor_only_until_safety: true}
        """,
    )
    needs = write(
        tmp_path,
        "needs.csv",
        """
        category,need_id,need,what counts
        Bangalore,BLR1,Civic,x
        Politics,POL1,National,y
        """,
    )
    return Taxonomy.load(cats, needs, write(tmp_path, "topics.csv", topics)), feeds


TOPICS_OK = """
topic_id,category,priority,geography,need_ids,editor_only
B01,Bangalore,High,Bengaluru,BLR1,
P13,Politics,Medium,Pan-India,POL1; BLR1,yes
"""


def test_categories_merge_defaults_with_overrides(tmp_path):
    tax, _ = small(tmp_path, TOPICS_OK)
    b, p = tax.categories["Bangalore"], tax.categories["Politics"]
    assert (b.quota_min, b.quota_max, b.prefilter_cap, b.editor_only_until_safety) == (4, 4, 60, False)
    assert (p.quota_min, p.quota_max, p.prefilter_cap, p.editor_only_until_safety) == (0, 3, 30, True)


def test_lookups(tmp_path):
    tax, _ = small(tmp_path, TOPICS_OK)
    assert tax.category_of_topic("P13") == "Politics" and tax.category_of_topic("zz") is None
    assert tax.needs_of_topic("P13") == ("POL1", "BLR1")
    assert tax.topics_for_need("BLR1") == ["B01", "P13"]
    assert tax.editor_only_topics() == {"P13"}
    assert tax.validate() == []


def test_a_need_without_a_topic_is_reported(tmp_path):
    tax, _ = small(tmp_path, "topic_id,category,need_ids\nB01,Bangalore,BLR1\n")
    assert tax.validate() == ["need POL1 (National) has no topic"]


def test_unknown_category_unknown_need_and_empty_need_ids_are_reported(tmp_path):
    tax, _ = small(
        tmp_path,
        "topic_id,category,need_ids\nB01,Bangalore,BLR1\nP13,Politics,POL1\nX1,Sports,BLR1\nX2,Bangalore,\nX3,Bangalore,ZZZ9\n",
    )
    problems = tax.validate()
    assert "topic X1 has the unknown category 'Sports'" in problems
    assert "topic X2 serves no need (need_ids is empty)" in problems
    assert "topic X3 names the unknown need ZZZ9" in problems


def test_old_topics_csv_without_the_new_columns_still_loads(tmp_path):
    tax, _ = small(tmp_path, "topic_id,category\nB01,Bangalore\n")
    assert tax.needs_of_topic("B01") == () and not tax.editor_only_topics()


def test_sources_must_serve_every_need(tmp_path):
    tax, _ = small(tmp_path, TOPICS_OK)
    feeds = write(
        tmp_path,
        "feeds.csv",
        """
        source_id,category,route_type,notes
        S1,Bangalore,Native publisher RSS/Atom,needs: BLR1. 28 Oct browser test: live
        S2,Sports,Native publisher RSS/Atom,needs: ZZZ9
        S3,Politics,Manual,needs: POL1
        """,
    )
    problems = tax.validate(feeds)
    assert "need POL1 (National) has no source" in problems  # S3 is manual, so it does not count
    assert "source S2 names the unknown need ZZZ9" in problems
    assert "source S2 has the unknown category 'Sports'" in problems


def test_parsing_helpers():
    assert split_ids("BLR1; BLR3 ,POL1") == ("BLR1", "BLR3", "POL1")
    assert needs_in_notes("Native feed. needs: BLR1; BLR3. 28 Oct browser test: live") == ("BLR1", "BLR3")
    assert needs_in_notes("no needs here") == ()


def test_the_real_files_agree_on_categories_needs_topics_and_sources(repo_root):
    tax = Taxonomy.load(repo_root / "config" / "categories.yaml", repo_root / "needs.csv", repo_root / "topics.csv")
    assert len(tax.categories) == 18 and len(tax.needs) == 75 and len(tax.topics) == 183
    assert tax.validate(repo_root / "feeds.csv") == []  # every need has a topic and a source


def test_editor_only_topics_never_serve_a_need_alone(repo_root):
    tax = Taxonomy.load(repo_root / "config" / "categories.yaml", repo_root / "needs.csv", repo_root / "topics.csv")
    only = tax.editor_only_topics()
    for need in tax.needs:
        assert set(tax.topics_for_need(need)) - only, need
