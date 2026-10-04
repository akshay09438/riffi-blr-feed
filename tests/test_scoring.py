import csv

import pytest

from riffi_ingest.scoring import Scorer, StoryFacts, TopicFacts


@pytest.fixture(scope="module")
def scorer():
    from tests.conftest import REPO_ROOT

    return Scorer.load(REPO_ROOT / "config" / "scoring.yaml")


def topic(priority="High", geography="Bengaluru", sensitive=False, tid="T1"):
    return TopicFacts(tid, priority, geography, sensitive)


def test_the_brief_table_exactly(scorer):
    # High (30) + Bengaluru (25) + new (15) + angle (10) + 3+ sources (10) + Official (10) = 100
    full = StoryFacts([topic()], ["Official", "Media", "Aggregator"], new_development=True, debate_angle=True)
    s = scorer.score(full)
    assert s.parts == {
        "priority": 30,
        "geography": 25,
        "new_development": 15,
        "debate_angle": 10,
        "source_spread": 10,
        "tier": 10,
    }
    assert s.total == 100 and s.label == "High" and not s.awaiting_ai
    for priority, points in (("High", 30), ("Medium", 20), ("Low", 10)):
        assert scorer.priority_points(priority) == points
    for geo, points in (("Bengaluru", 25), ("Karnataka", 20), ("South India", 12), ("Pan-India", 10)):
        assert scorer.geography_points(geo) == points
    assert [scorer.spread_points(n) for n in (1, 2, 3, 7)] == [0, 5, 10, 10]
    assert [scorer.tier_points([t]) for t in ("Official", "Media", "Aggregator")] == [10, 6, 3]


@pytest.mark.parametrize(
    "total,label",
    [(100, "High"), (70, "High"), (69, "Medium"), (45, "Medium"), (44, "Low"), (20, "Low"), (19, "Drop"), (0, "Drop")],
)
def test_label_thresholds(scorer, total, label):
    assert scorer.label(total) == label


def test_without_the_ai_pass_the_ai_points_are_zero_and_marked(scorer):
    s = scorer.score(StoryFacts([topic()], ["Media", "Media"]))
    assert s.parts["new_development"] == 0 and s.parts["debate_angle"] == 0 and s.awaiting_ai
    assert s.total == 30 + 25 + 5 + 6


def test_the_best_topic_and_the_best_tier_count(scorer):
    s = scorer.score(
        StoryFacts(
            [topic("Low", "Pan-India", tid="A"), topic("Medium", "Karnataka", tid="B")],
            ["Aggregator", "Official (party)"],
        )
    )
    assert s.best_topic == "B" and s.parts["priority"] == 20 and s.parts["geography"] == 20 and s.parts["tier"] == 10


def test_excluded_is_drop_and_sensitive_is_flagged(scorer):
    excluded = StoryFacts([topic()], ["Official"] * 3, new_development=True, debate_angle=True, excluded=True)
    assert scorer.score(excluded).label == "Drop" and scorer.score(excluded).total == 100
    sensitive = scorer.score(StoryFacts([topic(sensitive=True)], ["Media"]))
    assert sensitive.sensitive and sensitive.label != "Drop"


def test_an_untagged_story_scores_on_sources_only(scorer):
    s = scorer.score(StoryFacts([], ["Media", "Official", "Aggregator"]))
    assert s.best_topic is None and s.total == 20 and s.label == "Low"


def test_every_real_geography_and_tier_value_maps_to_points(scorer, repo_root):
    with open(repo_root / "topics.csv", encoding="utf-8") as f:
        geographies = {r["geography"] for r in csv.DictReader(f)}
    with open(repo_root / "feeds.csv", encoding="utf-8") as f:
        tiers = {r["tier"] for r in csv.DictReader(f)}
    assert all(scorer.geography_points(g) > 0 for g in geographies), [
        g for g in geographies if not scorer.geography_points(g)
    ]
    assert all(scorer.tier_points([t]) > 0 for t in tiers), [t for t in tiers if not scorer.tier_points([t])]
    assert scorer.geography_points("Pan-India (Bangalore angle)") == 10  # the first place named counts
    assert scorer.geography_points("Bangalore / Pan-India") == 25
    assert scorer.geography_points("Tamil Nadu / Bangalore interest") == 12
    assert scorer.tier_points(["Media (Kannada)"]) == 6
