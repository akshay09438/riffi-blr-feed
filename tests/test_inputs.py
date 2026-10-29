"""The three input files match what BRIEF.md promises. A failure here means a CSV was edited in a way
the engine's assumptions do not cover yet: update the brief/decisions and these numbers together."""

import csv
from collections import Counter

import riffi_ingest

CATEGORIES = {
    "Bangalore",
    "Politics",
    "Sports",
    "Entertainment",
    "Tech/AI",
    "Corporate",
    "Finance",
    "Stock market",
    "Education",
    "College life",
    "Relationships",
    "Social life",
    "General",
    "Growth",
    "Health & fitness",
    "Travel",
    "Fashion & style",
    "Beauty & grooming",
}  # the 18 Riffi content categories (D-017)

ROUTE_TYPES = {
    "Google News RSS": 125,  # 64 + 61 new (D-017: the PRD cap of 60, plus S282 replacing a blocked feed)
    "X/Instagram via RSS.app": 25,
    "Web page monitor": 16,  # 17 - S131 retired 29 Oct
    "Native publisher RSS/Atom": 101,  # 19 + 90 new - 6 retired after the 28 Oct live test - S061, S244 retired 29 Oct
    "RSSHub Telegram": 3,
    "YouTube Atom": 2,
    "Manual": 1,
}


def read(repo_root, name):
    with open(repo_root / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_package_imports():
    assert riffi_ingest.__version__


def test_feeds_has_273_sources_with_the_planned_route_types(repo_root):
    feeds = read(repo_root, "feeds.csv")
    retired = read(repo_root, "docs/prd/retired_sources.csv")
    # 131 from the brief + 151 added for D-017 (Phase 1) = S001..S282; 6 retired after the 28 Oct live test, 3 more on 29 Oct
    assert len(feeds) == 273
    assert len({r["source_id"] for r in feeds}) == 273
    assert sorted(r["source_id"] for r in feeds + retired) == [f"S{n:03d}" for n in range(1, 283)]
    assert {r["source_id"] for r in retired}.isdisjoint(r["source_id"] for r in feeds)
    assert all(r["reason"] for r in retired)
    assert Counter(r["route_type"] for r in feeds) == ROUTE_TYPES


def test_every_source_has_one_of_the_18_categories_and_a_need(repo_root):
    feeds = read(repo_root, "feeds.csv")
    needs = {r["need_id"]: r["category"] for r in read(repo_root, "needs.csv")}
    assert {r["category"] for r in feeds} <= CATEGORIES
    for r in feeds:
        if r["route_type"] == "Manual":
            continue
        listed = r["notes"].split("needs:")[-1].split(".")[0].replace(";", " ").split()
        assert listed and set(listed) <= set(needs), (r["source_id"], r["notes"])


def test_needs_has_75_needs_in_the_18_categories(repo_root):
    needs = read(repo_root, "needs.csv")
    assert len(needs) == 75 and len({r["need_id"] for r in needs}) == 75
    assert {r["category"] for r in needs} == CATEGORIES


def test_no_two_sources_share_a_feed_url(repo_root):
    # S015 and S076 share a Google News query from the original list; only rows added since (S132+) are held to this
    rows = [r for r in read(repo_root, "feeds.csv") if r["fetch_url"].startswith("http")]
    count = Counter(r["fetch_url"].strip().lower() for r in rows)
    dupes = [
        r["source_id"] for r in rows if int(r["source_id"][1:]) > 131 and count[r["fetch_url"].strip().lower()] > 1
    ]
    assert not dupes, dupes


def test_no_source_is_on_the_blocklist(repo_root):
    domains = {
        d.strip().lower()
        for r in read(repo_root, "blocklist.csv")
        for d in r["url"].split("/")
        if "." in d and " " not in d
    }
    for r in read(repo_root, "feeds.csv"):
        host = r["fetch_url"].split("/")[2].lower() if r["fetch_url"].startswith("http") else ""
        assert host.removeprefix("www.") not in domains or host.startswith("news.google"), r["source_id"]


def test_exactly_five_x_rows_are_priority_feeds(repo_root):
    feeds = read(repo_root, "feeds.csv")
    priority = sorted(r["source_id"] for r in feeds if r["priority_x_feed"].strip().lower() == "yes")
    assert priority == ["S001", "S024", "S026", "S028", "S035"]


def test_topics_has_184_topics_151_briefed_plus_33_for_d017(repo_root):
    topics = read(repo_root, "topics.csv")
    assert len(topics) == 184
    assert len({r["topic_id"] for r in topics}) == 184
    kinds = Counter(r["topic_id"][0] for r in topics)
    assert kinds["D"] == 36
    assert kinds["O"] == 28
    assert len(topics) - kinds["D"] - kinds["O"] == 120  # evergreen: B, K, SI, P (87 briefed + 33 added, D-017, D-018)
    assert {r["priority"] for r in topics} == {"High", "Medium", "Low"}


def test_every_topic_has_one_of_the_18_categories_and_known_needs(repo_root):
    topics = read(repo_root, "topics.csv")
    needs = {r["need_id"] for r in read(repo_root, "needs.csv")}
    for r in topics:
        assert r["category"] in CATEGORIES, (r["topic_id"], r["category"])
        ids = [x.strip() for x in r["need_ids"].split(";") if x.strip()]
        assert ids and set(ids) <= needs, (r["topic_id"], r["need_ids"])
        assert r["editor_only"] in ("", "yes")
        assert r["old_category"]  # the label before D-017, kept for the record


def test_d017_editor_only_topics_are_marked(repo_root):
    marked = {r["topic_id"] for r in read(repo_root, "topics.csv") if r["editor_only"] == "yes"}
    # caste/reservation, language and locals-vs-migrants, leaders and parties, and the cases D-017 added
    assert marked == {
        "O02", "O03", "O04", "O05", "O14", "O15", "O18", "O23", "O26", "O27",
        "B21", "B22", "B43", "B44", "K03", "K04", "K05", "P05", "P15", "P24", "P58", "SI03",
    }  # fmt: skip


def test_blocklist_has_its_columns(repo_root):
    rows = read(repo_root, "blocklist.csv")
    assert rows
    assert set(rows[0]) == {"name", "url", "reason"}
