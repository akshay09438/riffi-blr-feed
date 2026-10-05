"""The three input files match what BRIEF.md promises. A failure here means a CSV was edited in a way
the engine's assumptions do not cover yet: update the brief/decisions and these numbers together."""

import csv
from collections import Counter

import riffi_ingest

ROUTE_TYPES = {
    "Google News RSS": 59,
    "X/Instagram via RSS.app": 25,
    "Web page monitor": 22,
    "Native publisher RSS/Atom": 19,
    "RSSHub Telegram": 3,
    "YouTube Atom": 2,
    "Manual": 1,
}


def read(repo_root, name):
    with open(repo_root / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_package_imports():
    assert riffi_ingest.__version__


def test_feeds_has_131_sources_with_the_briefed_route_types(repo_root):
    feeds = read(repo_root, "feeds.csv")
    assert len(feeds) == 131
    assert len({r["source_id"] for r in feeds}) == 131
    assert Counter(r["route_type"] for r in feeds) == ROUTE_TYPES


def test_exactly_five_x_rows_are_priority_feeds(repo_root):
    feeds = read(repo_root, "feeds.csv")
    priority = sorted(r["source_id"] for r in feeds if r["priority_x_feed"].strip().lower() == "yes")
    assert priority == ["S001", "S024", "S026", "S028", "S035"]


def test_topics_has_151_topics_split_as_briefed(repo_root):
    topics = read(repo_root, "topics.csv")
    assert len(topics) == 151
    assert len({r["topic_id"] for r in topics}) == 151
    kinds = Counter(r["topic_id"][0] for r in topics)
    assert kinds["D"] == 36
    assert kinds["O"] == 28
    assert len(topics) - kinds["D"] - kinds["O"] == 87  # evergreen: B, K, SI, P
    assert {r["priority"] for r in topics} == {"High", "Medium", "Low"}


def test_blocklist_has_its_columns(repo_root):
    rows = read(repo_root, "blocklist.csv")
    assert rows
    assert set(rows[0]) == {"name", "url", "reason"}
