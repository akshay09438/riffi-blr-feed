"""Round 2 of the Daily Picks (28 Oct 2026, from the first real run on the laptop): freshness, near-duplicates,
finance framing, editor-only debate credit, per-category AI coverage, the extras-only batch (`extras-batch`),
and the topics that let Health, Growth, Fashion and Beauty stories pass. Written before the code."""

import json
from datetime import timedelta

import pytest
from test_picks import NOW, H, checked, run, story

from riffi_ingest.db import connect
from riffi_ingest.db.importers import import_sources, import_topics
from riffi_ingest.filter.config import FilterConfig
from riffi_ingest.filter.prefilter import choose_for_ai
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


FULL = {
    "daily_life": 14,
    "culture": 8,
    "audience_fit": 6,
    "side_a": "A side",
    "side_b": "B side",
    "hook_score": 10,
    "visual": 8,
}


def picks_by_id(r):
    return {a.story.story_id: a for a in r.assessment.assessed}


# ---- 4. freshness: nothing first reported more than 72 hours ago is picked


def test_a_story_first_reported_more_than_72_hours_ago_is_never_picked(db, tax, cfg, tmp_path):
    story(
        db,
        "old",
        "Namma Metro fares to rise",
        fetched=NOW - H,
        started=NOW - 7 * 24 * H,
        topics=("D05",),
        sources=("S011", "S034", "S063"),
    )
    story(
        db,
        "new",
        "Metro feeder buses start",
        fetched=NOW - H,
        started=NOW - 5 * H,
        topics=("D05",),
        sources=("S011", "S034", "S063"),
    )
    r, _ = run(db, tax, cfg, tmp_path, facts={"old": FULL, "new": FULL})
    by = picks_by_id(r)
    assert not by["old"].picked and "72 hours" in by["old"].reason
    assert by["new"].picked
    edge = story  # exactly 72 h is still fresh, 73 h is not
    edge(
        db,
        "e72",
        "Pink Line opening date",
        fetched=NOW - H,
        started=NOW - 72 * H,
        topics=("O08",),
        sources=("S011", "S034", "S063"),
    )
    edge(
        db,
        "e73",
        "Pink Line test run",
        fetched=NOW - H,
        started=NOW - 73 * H,
        topics=("O09",),
        sources=("S011", "S034", "S063"),
    )
    r, _ = run(db, tax, cfg, tmp_path, facts={"e72": FULL, "e73": FULL})
    by = picks_by_id(r)
    assert by["e72"].picked and not by["e73"].picked


# ---- 5. near-duplicates


def pair(
    db,
    a,
    b,
    topic="O13",
    started_a=None,
    started_b=None,
    sources_a=("S011", "S034"),
    sources_b=("S011", "S034", "S063"),
):
    story(db, "dupA", a, fetched=NOW - H, started=started_a or NOW - 3 * H, topics=(topic,), sources=sources_a)
    story(db, "dupB", b, fetched=NOW - H, started=started_b or NOW - 4 * H, topics=(topic,), sources=sources_b)


@pytest.mark.parametrize(
    "a,b",
    [
        (
            "BBMP seals 14 PGs in Koramangala over fire norms",
            "Officials seal 14 PG accommodations in Koramangala; fire safety lapses cited",
        ),
        (
            "Railways bans phone charging on trains overnight",
            "Overnight phone charging ban on trains: what Railways says",
        ),
        (
            "Bengaluru tunnel road bids opened, Adani lowest",
            "Tunnel road bids opened: Adani lowest bidder for Bengaluru project",
        ),
    ],
)
def test_near_duplicates_keep_the_story_with_more_sources(db, tax, cfg, tmp_path, a, b):
    pair(db, a, b, topic="O13")
    r, _ = run(db, tax, cfg, tmp_path, facts={"dupA": FULL, "dupB": FULL})
    by = picks_by_id(r)
    assert by["dupB"].picked and not by["dupA"].picked  # dupB has three sources, dupA two
    assert "near-duplicate" in by["dupA"].reason and "dupB" in by["dupA"].reason


def test_different_stories_on_the_same_topic_are_not_merged(db, tax, cfg, tmp_path):
    pair(
        db, "Tunnel road bids opened, Adani lowest", "Metro fares to rise from Monday for daily commuters", topic="O13"
    )
    r, _ = run(db, tax, cfg, tmp_path, facts={"dupA": FULL, "dupB": FULL})
    by = picks_by_id(r)
    assert by["dupA"].picked and by["dupB"].picked


def test_identical_wording_is_one_story_even_a_day_apart(db, tax, cfg, tmp_path):
    pair(
        db,
        "Metro fares to rise from Monday",
        "Metro fares to rise from Monday",
        topic="O13",
        started_a=NOW - 2 * H,
        started_b=NOW - 40 * H,
    )
    r, _ = run(db, tax, cfg, tmp_path, facts={"dupA": FULL, "dupB": FULL})
    by = picks_by_id(r)
    assert by["dupB"].picked != by["dupA"].picked  # exactly one of the two


def test_looser_matches_need_the_same_topic_and_the_same_day(db, tax, cfg, tmp_path):
    a, b = "Fire at Koramangala PG: 14 rescued", "Koramangala: 14 shifted after PG blaze"
    pair(db, a, b, topic="O13", started_a=NOW - 3 * H, started_b=NOW - 4 * H)
    r, _ = run(db, tax, cfg, tmp_path, facts={"dupA": FULL, "dupB": FULL})
    assert sum(x.picked for x in picks_by_id(r).values()) == 1  # same day: one story
    for sid in ("dupA", "dupB"):
        db.execute("DELETE FROM item_topics WHERE cluster_id = ?", (sid,))
        db.execute("DELETE FROM items WHERE cluster_id = ?", (sid,))
        db.execute("DELETE FROM story_clusters WHERE cluster_id = ?", (sid,))
    db.commit()
    pair(db, a, b, topic="O13", started_a=NOW - 3 * H, started_b=NOW - 30 * H)  # a day apart
    r, _ = run(db, tax, cfg, tmp_path, facts={"dupA": FULL, "dupB": FULL})
    assert sum(x.picked for x in picks_by_id(r).values()) == 2


# ---- 6. finance framing: never investment advice


@pytest.mark.parametrize(
    "field,text",
    [
        ("side_a", "Buy now before the rally"),
        ("side_b", "Sell your holdings today"),
        ("hook", "Time to invest in small caps"),
        ("hook", "Should you buy Nifty ETFs? Yes"),
        ("side_a", "Good time to invest in gold"),
        ("hook", "Stock pick: this multibagger will double"),
        ("side_b", "Hold or sell? Hold"),
        ("hook", "Chase gains or hold? What the Jio listing means"),
        ("side_a", "Should retail investors hold on to Jio shares"),
        ("side_b", "Can Indian investors get a slice of the Anthropic IPO"),
    ],
)
def test_finance_picks_whose_sides_or_hook_read_as_advice_are_rejected(db, tax, cfg, tmp_path, field, text):
    story(
        db,
        "f1",
        "Sensex falls 900 points as FIIs sell",
        fetched=NOW - H,
        topics=("P38",),
        sources=("S011", "S034", "S063"),
    )
    r, _ = run(db, tax, cfg, tmp_path, facts={"f1": {**FULL, field: text}})
    a = picks_by_id(r)["f1"]
    assert not a.picked and "advice" in a.reason


def test_a_debate_about_the_system_is_fine_and_buy_now_pay_later_is_not_advice(db, tax, cfg, tmp_path):
    story(
        db,
        "f2",
        "Sensex falls 900 points as FIIs sell",
        fetched=NOW - H,
        topics=("P38",),
        sources=("S011", "S034", "S063"),
    )
    story(
        db,
        "f3",
        "RBI tightens rules on buy now pay later apps",
        fetched=NOW - H,
        topics=("P36",),
        sources=("S011", "S034", "S063"),
    )
    ok = {
        **FULL,
        "side_a": "Markets are overhyped",
        "side_b": "Retail investors are the ones who lose",
        "hook": "Rs 4 lakh crore gone in a day",
    }
    bnpl = {**FULL, "side_a": "Buy now pay later helps first-time buyers", "side_b": "Buy now pay later hides debt"}
    r, _ = run(db, tax, cfg, tmp_path, facts={"f2": ok, "f3": bnpl})
    by = picks_by_id(r)
    assert by["f2"].picked and by["f3"].picked


def test_the_advice_check_applies_only_to_finance_and_markets(db, tax, cfg, tmp_path):
    story(
        db,
        "sale",
        "Myntra end of season sale starts",
        fetched=NOW - H,
        topics=("P50",),
        sources=("S011", "S034", "S063"),
    )
    r, _ = run(db, tax, cfg, tmp_path, facts={"sale": {**FULL, "hook": "Buy now: 70% off"}})
    assert picks_by_id(r)["sale"].picked


# ---- 7. editor-only stories: the withheld sides must not cost them the debate points


def test_an_editor_only_story_with_an_ai_debate_angle_gets_full_debate_credit(db, tax, cfg, tmp_path):
    story(
        db,
        "pol",
        "Cabinet reshuffle: who gets the finance portfolio",
        fetched=NOW - H,
        topics=("O03",),
        sources=("S011", "S034", "S063"),
    )
    checked(db, "pol", angle="Is the reshuffle about merit or arithmetic?")
    no_sides = {
        "daily_life": 6,
        "culture": 6,
        "audience_fit": 4,
        "hook_score": 8,
        "visual": 4,
    }  # sides withheld on purpose
    r, _ = run(db, tax, cfg, tmp_path, facts={"pol": no_sides})
    a = picks_by_id(r)["pol"]
    assert a.safety_state == "editor_only" and a.k_parts["debate"] == 35
    story(db, "gen", "Union Budget: new tax slabs", fetched=NOW - H, topics=("P44",), sources=("S011", "S034", "S063"))
    checked(db, "gen", angle="Who gains from the new slabs?")
    r, _ = run(db, tax, cfg, tmp_path, facts={"gen": no_sides})
    assert picks_by_id(r)["gen"].k_parts["debate"] == 15  # not editor-only: missing sides still cost points


POLITICS = [
    "Cabinet reshuffle: who gets the finance portfolio",
    "HD Kumaraswamy attacks the state over HMT land",
    "Opposition plans a padayatra on water tariffs",
]


def test_editor_only_stories_that_pass_both_gates_are_picked_and_shown_in_picks_md(db, tax, cfg, tmp_path):
    from riffi_ingest.outputs import picks as picks_out

    for i, t in enumerate(("O02", "O03", "O05")):
        story(db, f"p{i}", POLITICS[i], fetched=NOW - H, topics=(t,), sources=("S011", "S034", "S063"))
        checked(db, f"p{i}", angle="Is it merit or arithmetic?")
    r, folder = run(
        db,
        tax,
        cfg,
        tmp_path,
        facts={
            f"p{i}": {"daily_life": 8, "culture": 6, "audience_fit": 5, "hook_score": 8, "visual": 5} for i in range(3)
        },
    )
    assert len([a for a in r.selection.picks if a.category == "Politics"]) == 3  # Politics maximum
    picks_out.write_picks(r, folder, tax, cfg)
    assert "## Politics" in (folder / "picks.md").read_text(encoding="utf-8")
    assert json.loads((folder / "picks.json").read_text(encoding="utf-8"))["picks"] == []


def test_the_report_counts_stories_that_pass_both_gates_but_miss_the_pick_score_floor(db, tax, cfg, tmp_path):
    from riffi_ingest.outputs import picks as picks_out

    story(
        db,
        "low",
        "Metro feeder buses start",
        fetched=NOW - 20 * H,
        started=NOW - 60 * H,
        topics=("B13",),
        sources=("S011",),
    )
    r, folder = run(
        db,
        tax,
        cfg,
        tmp_path,
        facts={"low": {"daily_life": 8, "culture": 4, "audience_fit": 4, "side_a": "A", "side_b": "B"}},
    )
    picks_out.write_picks(r, folder, tax, cfg)
    report = (folder / "filter_report.md").read_text(encoding="utf-8")
    assert (
        "Passed both gates" in report and "Reached the pick-score floor" in report and "Top reason not picked" in report
    )


# ---- 2. per-category coverage for the AI pass


def test_every_category_with_stories_gets_its_minimum_in_the_ai_prefilter(db, tax, cfg, tmp_path):
    for i in range(80):  # Bangalore would take the whole budget on rank alone
        story(
            db, f"b{i:02d}", f"Metro news item {i}", fetched=NOW - H, topics=("D05",), sources=("S011", "S034", "S063")
        )
    others = {
        "Health & fitness": "P47",
        "Beauty & grooming": "P52",
        "Growth": "P46",
        "Fashion & style": "P50",
        "Stock market": "P38",
    }
    for name, topic in others.items():
        for i in range(12):
            story(db, f"{name[:3]}{i:02d}", f"{name} story {i}", fetched=NOW - H, topics=(topic,), sources=("S011",))
    r, _ = run(db, tax, cfg, tmp_path)
    smallcfg = type(cfg)({**cfg.raw, "prefilter": {**cfg["prefilter"], "max_stories": 100}})
    chosen = choose_for_ai(r.assessment.assessed, tax, smallcfg).chosen
    per = {}
    for a in chosen:
        per[a.category] = per.get(a.category, 0) + 1
    assert len(chosen) <= 100
    assert all(per.get(c, 0) >= 10 for c in others)  # minimum per category
    assert per["Bangalore"] <= tax.categories["Bangalore"].prefilter_cap == 60
    assert all(per[c] <= tax.categories[c].prefilter_cap for c in per)


# ---- 3. Health & fitness (and every other category) must be able to pass relevance


def test_every_need_has_a_topic_strong_enough_to_pass_relevance_with_a_modest_ai_judgement(tax, cfg):
    """The base points of a topic (priority + need + geography) must leave room for a normal AI judgement:
    a Pan-India Medium topic starts at 33, so a story needs 17 from the AI's three scores to reach 50.
    A Low-priority topic starts at 23 and almost never gets there, so each need needs a stronger one."""
    from riffi_ingest.filter.relevance import relevance

    def base(t):
        parts = relevance("x", t, None, cfg)[1]
        return parts["priority"] + parts["need_match"] + parts["geography"]

    weak = [
        need
        for need in tax.needs
        if max((base(tax.topics[t]) for t in tax.topics_for_need(need) if not tax.topics[t].editor_only), default=0)
        < 33
    ]
    assert weak == []


def test_fitness_gym_and_diet_trend_stories_get_a_topic_and_illness_stories_do_not_need_one(repo_root):
    from riffi_ingest.tagging.keywords import KeywordTagger

    tagger = KeywordTagger.load(repo_root / "config" / "topic_keywords.yaml")
    assert "P56" in tagger.tag("Gym memberships get costlier as strength training boom hits Indian cities").topics
    assert "P56" in tagger.tag("Home workout trend: why pickleball and padel are replacing the treadmill").topics
    assert "P57" in tagger.tag("Intermittent fasting and keto: the diet trends young Indians are following").topics
    assert "P47" in tagger.tag("FSSAI orders recall of packaged food over banned colour").topics
    assert not {"P56", "P57"} & set(tagger.tag("Dengue cases rise in Karnataka as hospitals report shortage").topics)


def test_the_new_health_topics_are_allowed_subjects_with_the_safety_note(repo_root):
    import csv

    with open(repo_root / "topics.csv", encoding="utf-8", newline="") as f:
        rows = {r["topic_id"]: r for r in csv.DictReader(f)}
    for tid in ("P56", "P57"):
        r = rows[tid]
        assert r["category"] == "Health & fitness" and r["editor_only"] == "" and r["priority"] in ("High", "Medium")
        assert "medical" in r["sensitive_note"].lower() and "illness" in r["sensitive_note"].lower()
    assert rows["P47"]["priority"] == "High" and rows["P38"]["priority"] == "High"
    for tid in ("P45", "P49", "P51", "P52", "P54"):
        assert rows[tid]["priority"] == "Medium"  # was Low: out of reach of the relevance bar


@pytest.mark.parametrize(
    "a,b",
    [
        (
            "Namma Metro Pink Line opening date announced",
            "Namma Metro fare hike from Monday: what changes for commuters",
        ),
        ("RBI keeps repo rate unchanged at 5.25 percent", "RBI tightens rules on buy now pay later apps"),
        ("Bengaluru FC beat Mumbai City 2-1 at Kanteerava", "Bengaluru FC sign a new striker from Spain"),
        ("Karnataka cabinet approves new rent control rules", "Karnataka cabinet clears Rs 900 crore for lake revival"),
        ("Zomato Q2 profit jumps 40 percent", "Swiggy Q2 loss narrows to Rs 625 crore"),
    ],
)
def test_distinct_stories_on_one_topic_and_day_are_not_called_duplicates(cfg, a, b):
    from datetime import datetime, timezone

    from riffi_ingest.filter.model import Assessed, StoryView
    from riffi_ingest.filter.similar import same_story
    from riffi_ingest.taxonomy import TopicInfo

    topic = TopicInfo("T1", "Bangalore", ("BLR3",), False, "High", "Bengaluru", "t")

    def mk(i, h):
        s = StoryView(
            i,
            h,
            datetime(2026, 10, 6, 5, tzinfo=timezone.utc),
            NOW,
            2,
            [],
            [],
            ["T1"],
            ["T1"],
            False,
            False,
            "",
            "",
            "",
        )
        return Assessed(s, topic, "Bangalore", "BLR3", "pending", {}, {}, 60, 60, 60.0, True, True, True)

    assert not same_story(mk("a", a), mk("b", b), cfg)
