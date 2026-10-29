"""Round 3 of the Daily Picks (29 Oct 2026, from the second real run on the laptop): an advice headline is never
picked in any category, picks come from news (not user posts, listings or trackers), lifestyle categories have
their own relevance pass mark, one pick per topic a day, and Politics gets a pick for the editor. Written before
the code."""

from datetime import timedelta

import pytest
from test_picks import NOW, H, run, story
from test_picks_round2 import FULL, picks_by_id

from riffi_ingest.db import connect
from riffi_ingest.db.importers import import_sources, import_topics
from riffi_ingest.filter.config import FilterConfig
from riffi_ingest.filter.facts import Facts
from riffi_ingest.filter.framing import advice_problem
from riffi_ingest.filter.sources_check import source_problem
from riffi_ingest.pipeline import Paths
from riffi_ingest.taxonomy import Taxonomy


@pytest.fixture
def db(tmp_path, repo_root):
    conn = connect(tmp_path / "engine.db")
    import_sources(conn, repo_root / "feeds.csv", now=NOW - timedelta(days=3))
    import_topics(conn, repo_root / "topics.csv", now=NOW - timedelta(days=3))
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def cfg():
    return FilterConfig.load(Paths().filter)


@pytest.fixture(scope="module")
def tax():
    p = Paths()
    return Taxonomy.load(p.categories, p.needs, p.topics)


# ---- advice in the headline, any category


@pytest.mark.parametrize(
    "headline",
    [
        "Nifty 50 heads for worst year in a decade: Should you buy stocks now or wait?",
        "Gold crashes 20%: time to buy?",
        "Jio listing: should investors hold or sell?",
    ],
)
def test_an_advice_headline_is_flagged_in_any_category(cfg, headline):
    assert "headline" in advice_problem("Tech/AI", None, cfg, headline)
    assert "headline" in advice_problem("General", Facts(side_a="Fair", side_b="Unfair"), cfg, headline)


@pytest.mark.parametrize(
    "headline",
    [
        "Jio Platforms valued at $108 billion on listing day",
        "Buy now, pay later apps face RBI scrutiny",
        "Sensex falls 900 points as IT stocks drag",
    ],
)
def test_a_news_headline_about_markets_is_fine(cfg, headline):
    assert advice_problem("Stock market", None, cfg, headline) == ""


def test_an_advice_headline_is_not_picked(db, tax, cfg, tmp_path):
    story(
        db,
        "adv",
        "Nifty worst year in a decade: should you buy stocks now or wait?",
        fetched=NOW - H,
        started=NOW - 3 * H,
        topics=("D05",),
        sources=("S011", "S034", "S063"),
    )
    r, _ = run(db, tax, cfg, tmp_path, facts={"adv": FULL})
    a = picks_by_id(r)["adv"]
    assert not a.picked and "investment advice" in a.reason


# ---- picks come from news


@pytest.mark.parametrize(
    "links",
    [
        ["https://www.reddit.com/r/bangalore/comments/abc/bengaluru_potholes/"],
        ["https://in.bookmyshow.com/events/gorillaz-live/ET001"],
        ["https://www.district.in/events/gorillaz-bengaluru-tickets"],
        ["https://example.in/kannada-box-office-collection-tracker"],
        ["https://old.reddit.com/r/bangalore/x", "https://insider.in/gorillaz/event"],
    ],
)
def test_user_posts_listings_and_trackers_are_not_news(cfg, links):
    assert source_problem(links, cfg)


@pytest.mark.parametrize(
    "links",
    [
        ["https://www.thehindu.com/news/cities/bangalore/pg-sealing/article1.ece"],
        ["https://www.reddit.com/r/bangalore/x", "https://www.deccanherald.com/city/pgs-to-be-sealed"],
        [],
    ],
)
def test_one_news_link_is_enough(cfg, links):
    assert source_problem(links, cfg) == ""


def test_a_reddit_only_story_is_not_picked(db, tax, cfg, tmp_path):
    story(
        db,
        "rd",
        "Bengaluru potholes are getting worse",
        fetched=NOW - H,
        started=NOW - 3 * H,
        topics=("D05",),
        sources=("S011",),
        url="https://www.reddit.com/r/bangalore/comments/abc/potholes/",
    )
    r, _ = run(db, tax, cfg, tmp_path, facts={"rd": FULL})
    a = picks_by_id(r)["rd"]
    assert not a.picked and "not news articles" in a.reason


# ---- lifestyle categories have their own relevance pass mark


def test_lifestyle_categories_have_a_lower_relevance_pass(tax, cfg):
    for name in ("Health & fitness", "Growth", "Fashion & style", "Beauty & grooming", "Relationships"):
        assert tax.categories[name].relevance_pass == 40
    assert tax.categories["Bangalore"].relevance_pass is None  # uses filter.yaml relevance.pass (50)


def test_a_fitness_trend_passes_relevance_at_the_category_mark(db, tax, cfg, tmp_path):
    soft = dict(FULL, daily_life=0, culture=6, audience_fit=2)  # a trend: little daily-life change
    story(
        db,
        "fit",
        "Run clubs are the new dating apps for Gen Z",
        fetched=NOW - H,
        started=NOW - 3 * H,
        topics=("P56",),
        sources=("S011", "S034"),
    )
    r, _ = run(db, tax, cfg, tmp_path, facts={"fit": soft})
    a = picks_by_id(r)["fit"]
    assert 40 <= a.r < 50  # would have failed the general bar of 50
    assert a.gate1


# ---- one pick per topic a day


def test_only_one_pick_per_topic(db, tax, cfg, tmp_path):
    for sid, head in (("t1", "Metro fares to rise from Monday"), ("t2", "Tunnel road bids opened, Adani lowest")):
        story(
            db,
            sid,
            head,
            fetched=NOW - H,
            started=NOW - 3 * H,
            topics=("O13",),
            sources=("S011", "S034", "S063"),
        )
    r, _ = run(db, tax, cfg, tmp_path, facts={"t1": FULL, "t2": FULL})
    by = picks_by_id(r)
    assert by["t1"].picked != by["t2"].picked
    loser = by["t2"] if by["t1"].picked else by["t1"]
    assert "already has 1 picks" in loser.reason


def test_politics_gets_at_least_one_pick_for_the_editor(tax):
    assert tax.categories["Politics"].quota_min == 1
