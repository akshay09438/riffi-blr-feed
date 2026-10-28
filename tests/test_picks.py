"""The daily-picks filter (D-017): facts sidecar, Gate 1 relevance, Gate 2 content-ability, the safety state,
the prefilter, the selection with quotas, the output files and the `picks` / `news-24h` commands.

Stories are written straight into a temporary database (helpers from test_outputs.py); the real config/*.yaml,
needs.csv and topics.csv are used, so a change to them that breaks the picks shows up here. data/ is never
touched (tests/conftest.py)."""

import csv
import json
from datetime import datetime, timedelta, timezone

import pytest
from test_outputs import NOW, H, story
from typer.testing import CliRunner

from riffi_ingest import cli
from riffi_ingest.db import connect
from riffi_ingest.db.connection import utc_iso
from riffi_ingest.db.importers import import_sources, import_topics
from riffi_ingest.filter import safety_state
from riffi_ingest.filter.config import FilterConfig
from riffi_ingest.filter.content import content, freshness_points, momentum_points
from riffi_ingest.filter.facts import Facts, clean, load_facts
from riffi_ingest.filter.model import load_stories
from riffi_ingest.filter.prefilter import choose_for_ai
from riffi_ingest.filter.relevance import best_topic, geography_points, relevance
from riffi_ingest.outputs import picks as picks_out
from riffi_ingest.outputs import queries
from riffi_ingest.pipeline import Paths
from riffi_ingest.taxonomy import Taxonomy

runner = CliRunner()


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


def good_facts(**over):
    base = dict(
        why_care="Your metro fare goes up",
        daily_life=14,
        culture=6,
        audience_fit=7,
        side_a="Fair, the metro needs money",
        side_b="Unfair, it pushes people to cars",
        hook="Rs 2,400 more a year",
        hook_score=12,
        visual=9,
        format="hot-take reel",
    )
    base.update(over)
    return Facts(**base)


def checked(conn, cid, whats_new="", angle=""):
    """A current AI verdict: the AI pass saw every report the story has now."""
    conn.execute(
        "INSERT OR REPLACE INTO ai_verdicts (cluster_id, is_new_development, whats_new, debate_angle, excluded,"
        " excluded_reason, reports_seen, batch_id, batch_written_at, checked_at)"
        " VALUES (?, ?, ?, ?, 0, '', (SELECT COUNT(*) FROM items WHERE cluster_id = ?), 'b', ?, ?)",
        (cid, int(bool(whats_new)), whats_new, angle, cid, utc_iso(NOW), utc_iso(NOW)),
    )
    # while a verdict is current, the story's topics are the AI's: it confirmed the keyword pass's guesses
    conn.execute(
        "INSERT OR IGNORE INTO item_topics (cluster_id, topic_id, match_method, tagged_at)"
        " SELECT cluster_id, topic_id, 'llm', tagged_at FROM item_topics WHERE cluster_id = ? AND match_method = 'keyword'",
        (cid,),
    )
    conn.commit()


def add_exclusion(conn, cid, reason="religious festival row"):
    conn.execute(
        "INSERT OR REPLACE INTO ai_verdicts (cluster_id, is_new_development, whats_new, debate_angle, excluded,"
        " excluded_reason, reports_seen, batch_id, batch_written_at, checked_at) VALUES (?, 0, '', '', 1, ?, 1, 'b', ?, ?)",
        (cid, reason, utc_iso(NOW), utc_iso(NOW)),
    )
    conn.commit()


# ---- the sidecar file


def test_facts_are_clamped_cleaned_and_limited():
    f = clean(
        {
            "why_care": "  Your \x00fare   goes\nup  ",
            "daily_life": 99,
            "culture": -4,
            "audience_fit": -99,
            "routine": "yes",
            "side_a": "x" * 500,
            "hook_score": True,
            "visual": 7.9,
            "format": "Hot-Take Reel",
        }
    )
    assert f.why_care == "Your fare goes up" and (f.daily_life, f.culture, f.audience_fit) == (20, 0, -15)
    assert f.routine is False and len(f.side_a) == 90 and f.hook_score == 0 and f.visual == 7
    assert f.format == "hot-take reel" and clean({"format": "tiktok dance"}).format == ""
    assert good_facts().two_sided and not good_facts(side_b="").two_sided and good_facts(side_b="").one_sided


def test_load_facts_reads_the_file_and_reports_problems(tmp_path):
    assert load_facts(tmp_path / "none.json") == ({}, [])
    path = tmp_path / "story_facts_v2.json"
    path.write_text(json.dumps({"c-1": {"daily_life": 5}, "c-2": "junk"}), encoding="utf-8")
    facts, problems = load_facts(path)
    assert facts["c-1"].daily_life == 5 and "c-2" not in facts and problems == ["c-2: not an object, ignored"]
    path.write_text(json.dumps({"stories": {"c-3": {"culture": 3}}}), encoding="utf-8")
    assert load_facts(path)[0]["c-3"].culture == 3
    for bad in ("{not json", "[1, 2]"):
        path.write_text(bad, encoding="utf-8")
        assert load_facts(path)[0] == {} and load_facts(path)[1]


# ---- Gate 1


def test_relevance_from_signals_alone(cfg, tax):
    metro = tax.topics["D05"]  # High, Bangalore (=Bengaluru), serves BLR3
    r, parts = relevance("Metro fare hike from Monday", metro, None, cfg)
    assert parts["priority"] == 25 and parts["need_match"] == 10 and parts["geography"] == 20
    assert parts["audience_fit"] == 5 and r == 60  # "metro" is a boost term
    r, parts = relevance("Pension reform for retirement savings", tax.topics["P35"], None, cfg)
    assert parts["audience_fit"] == -10 and parts["geography"] == 8
    r, parts = relevance("Mayor condoles the death of a veteran", metro, None, cfg)
    assert parts["routine"] == -15


def test_relevance_uses_the_ai_judgement_when_there_is_one(cfg, tax):
    r, parts = relevance("Fares", tax.topics["P35"], good_facts(), cfg)
    assert (parts["daily_life"], parts["culture"], parts["audience_fit"]) == (
        14,
        6,
        7,
    ) and r == 25 + 10 + 8 + 14 + 6 + 7
    assert relevance("x", tax.topics["P35"], good_facts(routine=True), cfg)[1]["routine"] == -15
    big = relevance("x", tax.topics["D05"], good_facts(daily_life=20, culture=15, audience_fit=10), cfg)[0]
    assert big == 100 or big <= 100


def test_geography_and_the_best_topic_do_not_depend_on_order(cfg, tax):
    assert geography_points("Pan-India (Bangalore angle)", cfg) == 8
    assert geography_points("Bangalore / Pan-India", cfg) == 20
    assert geography_points("Tamil Nadu", cfg) == 8 and geography_points("Mars", cfg) == 0
    a, b = tax.topics["D05"], tax.topics["O06"]  # same priority and place: the lower id wins
    assert best_topic([b, a], cfg).topic_id == "D05" == best_topic([a, b], cfg).topic_id
    assert best_topic([], cfg) is None


# ---- Gate 2


def kw(cfg, **over):
    base = dict(
        started_at=NOW - 2 * H, source_count=1, facts=None, ai_angle="", topic_has_angles=False, now=NOW, cfg=cfg
    )
    base.update(over)
    return content(**base)


def test_content_score_parts(cfg):
    assert freshness_points(NOW - 3 * H, NOW, cfg) == 15
    assert freshness_points(NOW - 30 * H, NOW, cfg) == 8 and freshness_points(NOW - 60 * H, NOW, cfg) == 0
    assert (momentum_points(1, cfg), momentum_points(2, cfg), momentum_points(3, cfg), momentum_points(9, cfg)) == (
        0,
        8,
        15,
        15,
    )
    k, parts = kw(cfg, facts=good_facts(), source_count=3)
    assert parts == {"debate": 35, "hook": 12, "freshness": 15, "momentum": 15, "visual": 9} and k == 86
    assert kw(cfg, facts=good_facts(side_b=""))[1]["debate"] == 15  # one-sided
    assert kw(cfg, ai_angle="Is a tunnel right?")[1]["debate"] == 15  # the AI verdict's own angle
    assert kw(cfg, topic_has_angles=True)[1]["debate"] == 15  # only when there are no AI facts at all
    assert kw(cfg, facts=Facts(), topic_has_angles=True)[1]["debate"] == 0
    assert kw(cfg)[1]["debate"] == 0


# ---- safety state


def sview(topic_ids, ai_checked=True):
    from riffi_ingest.filter.model import StoryView

    return StoryView("c-1", "h", NOW, NOW, 1, [], [], topic_ids, list(topic_ids), ai_checked, False, "", "", "")


def test_safety_state_is_fail_closed(tax):
    bangalore, politics = tax.categories["Bangalore"], tax.categories["Politics"]
    assert safety_state.safety_state(sview(["D05"]), bangalore, tax, gate0=False) == "pending"  # no filter built
    assert safety_state.safety_state(sview(["D05"]), bangalore, tax, gate0=True) == "cleared"
    assert safety_state.safety_state(sview(["D05"], ai_checked=False), bangalore, tax, gate0=True) == "pending"
    assert safety_state.safety_state(sview(["O02"]), bangalore, tax, gate0=True) == "editor_only"  # leader topic
    assert safety_state.safety_state(sview(["D05", "O05"]), bangalore, tax, gate0=True) == "editor_only"  # any topic
    assert safety_state.safety_state(sview(["D33"]), politics, tax, gate0=False) == "editor_only"
    assert safety_state.safety_state(sview(["D33"]), politics, tax, gate0=True) == "cleared"
    assert safety_state.safety_state(sview(["D33"], ai_checked=False), politics, tax, gate0=False) == "editor_only"


def test_gate0_is_not_shipped_until_the_safety_module_exists():
    # Proposal 4c adds riffi_ingest/safety/lexicon.py; until then nothing can be cleared for the seed agent.
    import importlib.util

    assert safety_state.gate0_shipped() == (importlib.util.find_spec("riffi_ingest.safety.lexicon") is not None)


# ---- a small day of stories


def day(db, now=NOW):
    """Eleven stories fetched in the last 24 h. Topics: D05 metro (Bangalore High), O06 tunnel (Bangalore High),
    P35 prices (Finance High, Pan-India), P42 placements, P37 scams, D01 cricket (Low), O02 leader (editor only)."""
    story(
        db,
        "m1",
        "Namma Metro fares to rise from Monday",
        fetched=now - 2 * H,
        topics=("D05",),
        sources=("S011", "S034", "S063"),
    )
    story(
        db, "m2", "Metro trains delayed on Purple Line", fetched=now - 3 * H, topics=("D05",), sources=("S011", "S034")
    )
    story(
        db,
        "m3",
        "Metro feeder buses start in Whitefield",
        fetched=now - 4 * H,
        topics=("D05",),
        sources=("S011", "S034"),
    )
    story(db, "t1", "Tunnel road bids opened", fetched=now - 2 * H, topics=("O06",), sources=("S011", "S034", "S063"))
    story(
        db,
        "p1",
        "Petrol and gold prices jump again",
        fetched=now - H,
        topics=("P35",),
        sources=("S011", "S034", "S063"),
    )
    story(
        db,
        "p2",
        "Placement season: highest package crosses Rs 2 crore",
        fetched=now - H,
        topics=("P42",),
        sources=("S011", "S034"),
    )
    story(
        db,
        "s1",
        "Digital arrest scam: RBI warns customers",
        fetched=now - 5 * H,
        topics=("P37",),
        sources=("S011", "S034"),
    )
    story(db, "c1", "India beat New Zealand in first T20I", fetched=now - 6 * H, topics=("D01",), sources=("S011",))
    story(
        db,
        "l1",
        "CM Shivakumar says metro will reach airport",
        fetched=now - 2 * H,
        topics=("O02",),
        sources=("S011", "S034", "S063"),
    )
    story(
        db,
        "x1",
        "Row over a religious festival",
        fetched=now - 2 * H,
        topics=("D05",),
        sources=("S011", "S034", "S063"),
    )
    add_exclusion(db, "x1")
    story(db, "n1", "A story that matches no tracked topic", fetched=now - 2 * H, topics=(), sources=("S011",))


def run(db, tax, cfg, tmp_path, *, gate0=False, facts=None, now=NOW):
    w = queries.window_for(now)
    folder = tmp_path / "out"
    if facts is not None:
        folder.mkdir(exist_ok=True)
        (folder / "story_facts_v2.json").write_text(json.dumps(facts), encoding="utf-8")
    return picks_out.build(db, w, tax, cfg, folder, now=now, gate0=gate0), folder


def test_loading_gives_topics_sources_links_and_the_verdict(db):
    day(db)
    checked(db, "m1", whats_new="Fares rise 10%")
    stories = {s.story_id: s for s in load_stories(db, queries.window_for(NOW))}
    m1 = stories["m1"]
    assert m1.topic_ids == ["D05"] and m1.ai_checked and m1.whats_new == "Fares rise 10%"
    assert len(m1.sources) == 3 and len(m1.links) == 3 and not m1.excluded
    assert stories["x1"].excluded and stories["x1"].excluded_reason == "religious festival row"
    assert stories["n1"].topic_ids == []


def test_excluded_and_untagged_stories_never_become_candidates(db, tax, cfg, tmp_path):
    day(db)
    r, _ = run(db, tax, cfg, tmp_path)
    ids = {a.story.story_id for a in r.assessment.assessed}
    assert "x1" not in ids and "n1" not in ids
    assert r.assessment.excluded == {"religious festival row": 1} and r.assessment.no_topic == 1


def test_without_ai_only_strong_signals_pass_and_everything_is_marked_ai_missing(db, tax, cfg, tmp_path):
    day(db)
    r, _ = run(db, tax, cfg, tmp_path)
    by = {a.story.story_id: a for a in r.assessment.assessed}
    assert all(a.ai_missing for a in by.values())
    assert by["m1"].gate1 and by["m1"].gate2 and by["m1"].picked  # Bangalore High, three sources, fresh
    assert not by["c1"].picked and "below" in by["c1"].reason  # a Low national topic with one source
    assert by["l1"].safety_state == "editor_only" and by["m1"].safety_state == "pending"  # no safety filter yet


def test_picks_are_deterministic(db, tax, cfg, tmp_path):
    day(db)
    first, _ = run(db, tax, cfg, tmp_path)
    second, _ = run(db, tax, cfg, tmp_path)
    assert [a.story.story_id for a in first.selection.picks] == [a.story.story_id for a in second.selection.picks]
    assert [(a.r, a.k, a.p) for a in first.selection.picks] == [(a.r, a.k, a.p) for a in second.selection.picks]


def test_ai_facts_lift_a_national_story_over_the_bar(db, tax, cfg, tmp_path):
    day(db)
    r0, _ = run(db, tax, cfg, tmp_path)
    p1_before = next(a for a in r0.assessment.assessed if a.story.story_id == "p1")
    r1, _ = run(
        db,
        tax,
        cfg,
        tmp_path,
        facts={
            "p1": {"daily_life": 18, "culture": 8, "audience_fit": 8, "side_a": "A", "side_b": "B", "hook_score": 10}
        },
    )
    p1 = next(a for a in r1.assessment.assessed if a.story.story_id == "p1")
    assert not p1.ai_missing and p1.r > p1_before.r and p1.k > p1_before.k and p1.picked


def test_quotas_per_topic_per_category_and_the_target(db, tax, cfg, tmp_path):
    day(db)
    r, _ = run(db, tax, cfg, tmp_path)
    picks = r.selection.picks
    metro = [a for a in picks if a.topic.topic_id == "D05"]
    assert len(metro) <= 2  # max_per_topic
    bangalore = [a for a in picks if a.category == "Bangalore"]
    assert len(bangalore) <= tax.categories["Bangalore"].quota_max
    assert len(picks) <= int(cfg["picks"]["target"])
    third = next(a for a in r.assessment.assessed if a.topic.topic_id == "D05" and not a.picked and a.gate1 and a.gate2)
    assert "already has" in third.reason
    assert any("Only" in n for n in r.selection.notes)  # fewer than 15 passed: said so, nothing padded


TRAFFIC = [
    "Silk Board flyover closed for repairs next week",
    "Hebbal junction loop opens to vehicles",
    "Airport road toll collection starts Monday",
    "Tumakuru road potholes cause three-hour jam",
    "Outer Ring Road bus priority lane extended",
    "Whitefield commuters demand a new metro feeder",
    "Marathahalli bridge widening gets a green signal",
    "Electronic City elevated expressway toll revised",
    "KR Puram hanging bridge reopens after months",
    "Sarjapur road signal timings revised by police",
    "Bellary road airport traffic advisory for Friday",
    "Bannerghatta road underpass work enters last phase",
]


def test_a_viral_category_cannot_take_the_whole_list(db, tax, cfg, tmp_path):
    for i in range(12):
        story(
            db,
            f"b{i}",
            TRAFFIC[i],
            fetched=NOW - H,
            topics=(("O06", "B03", "O09", "O10")[i % 4],),
            sources=("S011", "S034", "S063"),
        )
    r, _ = run(db, tax, cfg, tmp_path)
    assert sum(a.category == "Bangalore" for a in r.selection.picks) == 4


def test_tiers_follow_the_pick_score(db, tax, cfg, tmp_path):
    day(db)
    r, _ = run(db, tax, cfg, tmp_path)
    assert r.selection.picks
    for a in r.selection.picks:
        assert a.tier == ("High" if a.p >= 75 else "Medium" if a.p >= 60 else "Preview")
        assert a.p >= (50 if a.ai_missing else 60)  # picks.floor_ai_missing / tiers.Medium
        assert (a.tier == "Preview") <= a.ai_missing  # only a story with no AI judgement can be a Preview
    full = {
        "daily_life": 20,
        "culture": 15,
        "audience_fit": 10,
        "side_a": "A",
        "side_b": "B",
        "hook_score": 20,
        "visual": 15,
    }
    r2, _ = run(db, tax, cfg, tmp_path, facts={"m1": full})
    m1 = next(a for a in r2.selection.picks if a.story.story_id == "m1")
    assert m1.p >= 75 and m1.tier == "High" and not m1.ai_missing


# ---- prefilter


def test_prefilter_keeps_bangalore_high_and_caps_each_category(db, tax, cfg, tmp_path):
    for i in range(70):
        story(db, f"z{i:02d}", f"Metro news {i}", fetched=NOW - H, topics=("D05",), sources=("S011",))
    for i in range(40):
        story(db, f"y{i:02d}", f"Scam news {i}", fetched=NOW - H, topics=("P37",), sources=("S011",))
    r, _ = run(db, tax, cfg, tmp_path)
    chosen = r.prefilter.chosen
    assert sum(a.category == "Bangalore" for a in chosen) == tax.categories["Bangalore"].prefilter_cap == 60
    assert sum(a.category == "Finance" for a in chosen) == tax.categories["Finance"].prefilter_cap == 30
    assert r.prefilter.left_out == 20  # 10 Bangalore and 10 Finance stories beyond the categories' caps
    small = choose_for_ai(
        r.assessment.assessed, tax, FilterConfig({**cfg.raw, "prefilter": {**cfg["prefilter"], "max_stories": 5}})
    )
    assert len(small.chosen) == 5  # the AI pass takes at most max_stories, even for the stories always kept


# ---- outputs


def test_files_are_written_and_picks_json_is_empty_without_the_safety_filter(db, tax, cfg, tmp_path):
    day(db)
    r, folder = run(db, tax, cfg, tmp_path)
    picks_out.write_picks(r, folder, tax, cfg)
    assert {p.name for p in folder.iterdir()} == {
        "picks.md",
        "picks.csv",
        "picks.json",
        "candidates.csv",
        "filter_report.md",
    }
    assert json.loads((folder / "picks.json").read_text(encoding="utf-8"))["picks"] == []
    md = (folder / "picks.md").read_text(encoding="utf-8")
    assert "safety word-list filter is not built yet" in md and "Namma Metro fares" in md and "## Bangalore" in md
    with open(folder / "picks.csv", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == list(picks_out.PICK_FIELDS) and rows[0]["rank"] == "1"
    with open(folder / "candidates.csv", encoding="utf-8-sig", newline="") as f:
        cands = list(csv.DictReader(f))
    assert {c["story_id"] for c in cands} >= {"m1", "c1"} and all(c["reason"] for c in cands)
    assert "Filter report" in (folder / "filter_report.md").read_text(encoding="utf-8")


def test_excluded_stories_reach_no_file(db, tax, cfg, tmp_path):
    day(db)
    r, folder = run(db, tax, cfg, tmp_path)
    picks_out.write_picks(r, folder, tax, cfg)
    for name in ("picks.md", "picks.csv", "picks.json", "candidates.csv"):
        assert "religious festival" not in (folder / name).read_text(encoding="utf-8-sig")
    report = (folder / "filter_report.md").read_text(encoding="utf-8")
    assert "religious festival row: 1" in report  # a count of the reason, never the headline
    assert "Row over a religious festival" not in report


def test_only_cleared_picks_reach_picks_json_and_only_when_the_safety_filter_exists(db, tax, cfg, tmp_path):
    day(db)
    checked(db, "m1")  # the AI pass has checked m1 (not excluded)
    r, folder = run(db, tax, cfg, tmp_path, gate0=True)
    picks_out.write_picks(r, folder, tax, cfg)
    exported = json.loads((folder / "picks.json").read_text(encoding="utf-8"))["picks"]
    assert [p["story_id"] for p in exported] == ["m1"]  # the others are unchecked (pending) or editor-only
    assert all(p["safety_state"] == "cleared" for p in exported)
    md = (folder / "picks.md").read_text(encoding="utf-8")
    assert "safety word-list filter is not built yet" not in md


def test_text_from_the_ai_pass_cannot_carry_a_link_or_a_formula(db, tax, cfg, tmp_path):
    day(db)
    r, folder = run(
        db,
        tax,
        cfg,
        tmp_path,
        facts={
            "m1": {
                "why_care": "see https://evil.example/x now",
                "side_a": "=SUM(A1)",
                "side_b": "ok",
                "hook": "[click](http://e.co)",
                "daily_life": 15,
                "culture": 10,
                "audience_fit": 8,
                "hook_score": 10,
            }
        },
    )
    picks_out.write_picks(r, folder, tax, cfg)
    md = (folder / "picks.md").read_text(encoding="utf-8")
    assert "https://evil.example" not in md and "http://e.co" not in md
    with open(folder / "picks.csv", encoding="utf-8-sig", newline="") as f:
        row = next(r for r in csv.DictReader(f) if r["story_id"] == "m1")
    assert row["side_a"].startswith("'=")


def test_a_broken_sidecar_is_reported_and_ignored(db, tax, cfg, tmp_path):
    day(db)
    folder = tmp_path / "out"
    folder.mkdir()
    (folder / "story_facts_v2.json").write_text("{nope", encoding="utf-8")
    r = picks_out.build(db, queries.window_for(NOW), tax, cfg, folder, now=NOW, gate0=False)
    assert r.problems and all(a.ai_missing for a in r.assessment.assessed)
    picks_out.write_picks(r, folder, tax, cfg)
    assert "not valid JSON" in (folder / "filter_report.md").read_text(encoding="utf-8")


# ---- the commands


def invoke(db_path, tmp_path, *args):
    return runner.invoke(cli.app, [*args, "--db", str(db_path), "--out", str(tmp_path / "reports")])


def test_picks_command(db, tmp_path):
    day(db, datetime.now(timezone.utc))
    path = tmp_path / "engine.db"  # the db fixture's file
    r = runner.invoke(cli.app, ["picks", "--db", str(path), "--out", str(tmp_path / "reports")])
    assert r.exit_code == 0, r.output
    assert "Picks for" in r.output and "safety word-list filter is not built yet" in r.output
    folders = list((tmp_path / "reports").iterdir())
    assert len(folders) == 1 and (folders[0] / "picks.md").exists()


def test_picks_command_needs_a_database_with_articles(tmp_path):
    r = runner.invoke(cli.app, ["picks", "--db", str(tmp_path / "missing.db"), "--out", str(tmp_path / "r")])
    assert r.exit_code != 0 and "no database" in r.output


def test_news_24h_without_fetching_writes_batches_digest_and_picks(db, tmp_path):
    day(db, datetime.now(timezone.utc))
    path = tmp_path / "engine.db"
    ai = tmp_path / "ai"
    args = [
        "news-24h",
        "--skip-fetch",
        "--db",
        str(path),
        "--out",
        str(tmp_path / "reports"),
        "--ai-out",
        str(ai),
        "--records",
        str(tmp_path / "records"),
    ]
    r = runner.invoke(cli.app, args)
    assert r.exit_code == 0, r.output
    assert any(p.name.endswith("-extras") for p in ai.iterdir())  # the extras batch is written too
    assert ai.is_dir() and any(ai.iterdir())  # the AI batches go where --ai-out says, never into the project's data/
    assert "Digest for" in r.output and "Picks for" in r.output and "run: python -m riffi_ingest picks" in r.output
    folder = next((tmp_path / "reports").iterdir())
    assert {"digest.md", "digest.csv", "picks.md", "filter_report.md"} <= {p.name for p in folder.iterdir()}
    assert "## Stories by category" in (folder / "digest.md").read_text(encoding="utf-8")


def test_a_past_day_is_rebuilt_as_of_the_end_of_its_window(db, tmp_path):
    ist = timezone(timedelta(hours=5, minutes=30))
    d = (datetime.now(timezone.utc) - timedelta(days=2)).astimezone(ist).date()
    end = datetime(d.year, d.month, d.day, 7, 0, tzinfo=ist)
    day(db, end)
    args = ["picks", "--db", str(tmp_path / "engine.db"), "--out", str(tmp_path / "reports"), "--date", d.isoformat()]
    assert runner.invoke(cli.app, args).exit_code == 0
    path = tmp_path / "reports" / d.isoformat() / "picks.csv"
    first = path.read_text(encoding="utf-8-sig")
    assert "Namma Metro fares" in first
    assert runner.invoke(cli.app, args).exit_code == 0 and path.read_text(encoding="utf-8-sig") == first


def test_the_config_must_have_what_the_filter_needs(tmp_path):
    bad = tmp_path / "filter.yaml"
    bad.write_text("relevance: {pass: 50}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing"):
        FilterConfig.load(bad)


def test_a_full_day_with_ai_judgements_spreads_across_categories_and_honours_the_quotas(db, tax, cfg, tmp_path):
    """Golden-day shape: two well-covered stories in every category, the AI having judged them all."""
    facts = {}
    for name in tax.categories:
        topics = [
            t.topic_id
            for t in sorted(tax.topics.values(), key=lambda t: t.topic_id)
            if t.category == name and t.priority == "High" and not t.editor_only
        ][:2] or [t.topic_id for t in tax.topics.values() if t.category == name][:2]
        for n, tid in enumerate(topics):
            cid = f"g-{name[:3]}-{n}"
            story(
                db,
                cid,
                f"{name} headline number {n}",
                fetched=NOW - 2 * H,
                topics=(tid,),
                sources=("S011", "S034", "S063"),
            )
            checked(db, cid)
            facts[cid] = {
                "daily_life": 12, "culture": 8, "audience_fit": 5, "side_a": "A", "side_b": "B", "hook_score": 10, "visual": 8,
            }  # fmt: skip
    r, _ = run(db, tax, cfg, tmp_path, gate0=True, facts=facts)
    picks = r.selection.picks
    by_cat = {}
    for a in picks:
        by_cat[a.category] = by_cat.get(a.category, 0) + 1
    assert len(picks) >= 15 and len(by_cat) >= 10
    assert by_cat.get("Bangalore", 0) <= 4 and by_cat.get("Politics", 0) <= 3
    assert max(by_cat.values()) <= 4 and len(picks) <= 25
    assert len({a.story.story_id for a in picks}) == len(picks)  # never the same story twice
    again, _ = run(db, tax, cfg, tmp_path, gate0=True, facts=facts)
    assert [a.story.story_id for a in again.selection.picks] == [a.story.story_id for a in picks]
    assert not r.selection.notes  # 15 or more passed: no "only N" remark


# ---- ai-facts: the AI pass's extra judgements into the sidecar

from riffi_ingest.filter.facts_apply import apply_extras, extra_name  # noqa: E402
from riffi_ingest.tagging import llm_batches  # noqa: E402


def batches_for(db, tmp_path):
    day(db)
    return llm_batches.write_batches(db, tmp_path / "ai", since=NOW - timedelta(days=1), now=NOW)


def write_extra(run, n, entries, batch_id=None):
    batch = json.loads(run.batches[n - 1].read_text(encoding="utf-8"))
    path = run.folder / extra_name(batch["answer_file"])
    path.write_text(json.dumps({"batch_id": batch_id or batch["batch_id"], "extras": entries}), encoding="utf-8")
    return path


def test_extra_name():
    assert extra_name("answer-03.json") == "extra-03.json"


def test_extras_are_stored_cleaned_and_merged(db, tmp_path):
    run = batches_for(db, tmp_path)
    sidecar = tmp_path / "r" / "story_facts_v2.json"
    write_extra(
        run, 1, [{"story_id": "m1", "why_care": "Your fare rises", "daily_life": 99, "side_a": "A", "side_b": "B"}]
    )
    r = apply_extras(db, run.folder, sidecar)
    assert r.applied == 1 and not r.rejected
    facts, problems = load_facts(sidecar)
    assert facts["m1"].daily_life == 20 and facts["m1"].two_sided and problems == []
    write_extra(run, 1, [{"story_id": "t1", "culture": 4}, {"story_id": "m1", "daily_life": 3}])
    apply_extras(db, run.folder, sidecar)
    facts, _ = load_facts(sidecar)
    assert facts["m1"].daily_life == 3 and facts["t1"].culture == 4  # later replaces earlier; others kept


def test_extras_for_other_stories_wrong_batches_and_excluded_stories_are_refused(db, tmp_path):
    run = batches_for(db, tmp_path)
    sidecar = tmp_path / "r" / "story_facts_v2.json"
    # m2 was excluded after the batch was written: its extras are skipped
    add_exclusion(db, "m2")
    write_extra(
        run, 1,
        [{"story_id": "zz-not-here", "daily_life": 5}, {"story_id": "m2", "daily_life": 5}, {"story_id": "m1"}, {"story_id": "m1"}],
    )  # fmt: skip
    r = apply_extras(db, run.folder, sidecar)
    reasons = {(sid, why) for _, sid, why in r.rejected}
    assert ("zz-not-here", "not a story of this batch") in reasons and ("m1", "given twice in one file") in reasons
    assert r.skipped_excluded == 1 and r.applied == 0 and not sidecar.exists()
    write_extra(run, 1, [{"story_id": "m1"}], batch_id="another-batch")
    r = apply_extras(db, run.folder, sidecar)
    assert r.rejected and "batch_id" in r.rejected[0][2] and not sidecar.exists()


def test_a_folder_the_engine_did_not_write_is_refused(db, tmp_path):
    (tmp_path / "fake").mkdir()
    with pytest.raises(LookupError):
        apply_extras(db, tmp_path / "fake", tmp_path / "s.json")


def test_a_missing_or_broken_extra_file_is_reported(db, tmp_path):
    run = batches_for(db, tmp_path)
    r = apply_extras(db, run.folder, tmp_path / "s.json")
    assert r.files_missing == ["extra-01.json"] and r.applied == 0
    (run.folder / "extra-01.json").write_text("{nope", encoding="utf-8")
    r = apply_extras(db, run.folder, tmp_path / "s.json")
    assert r.rejected and "not valid JSON" in r.rejected[0][2]


def test_ai_facts_command_feeds_the_picks(db, tmp_path):
    day(db, datetime.now(timezone.utc))
    run = llm_batches.write_batches(
        db, tmp_path / "ai", since=datetime.now(timezone.utc) - timedelta(days=1), now=datetime.now(timezone.utc)
    )
    write_extra(
        run,
        1,
        [
            {
                "story_id": "p1",
                "daily_life": 18,
                "culture": 8,
                "audience_fit": 8,
                "side_a": "A",
                "side_b": "B",
                "hook_score": 10,
            }
        ],
    )
    db_path = tmp_path / "engine.db"
    r = runner.invoke(cli.app, ["ai-facts", str(run.folder), "--db", str(db_path), "--out", str(tmp_path / "reports")])
    assert r.exit_code == 0 and "1 stored" in r.output, r.output
    r = runner.invoke(cli.app, ["picks", "--db", str(db_path), "--out", str(tmp_path / "reports")])
    assert r.exit_code == 0, r.output
    folder = next((tmp_path / "reports").iterdir())
    with open(folder / "candidates.csv", encoding="utf-8-sig", newline="") as f:
        rows = {c["story_id"]: c for c in csv.DictReader(f)}
    assert rows["p1"]["ai_missing"] == "no" and rows["m1"]["ai_missing"] == "yes"
    r = runner.invoke(
        cli.app, ["ai-facts", str(tmp_path / "nope"), "--db", str(db_path), "--out", str(tmp_path / "reports")]
    )
    assert r.exit_code != 0 and "not a folder written by ai-batch" in r.output


# ---- found by the independent review (28 Oct 2026): each reproduced, then fixed

import sys  # noqa: E402
import types  # noqa: E402

from riffi_ingest.outputs.digest import _link  # noqa: E402


def test_a_stub_or_broken_safety_module_never_opens_the_gate(monkeypatch):
    name = "riffi_ingest.safety.lexicon"
    stub = types.ModuleType(name)
    monkeypatch.setitem(sys.modules, name, stub)
    assert safety_state.gate0_shipped() is False  # an empty file is not a safety filter
    stub.gate0_ready = lambda: "yes"
    assert safety_state.gate0_shipped() is False  # exactly True, nothing else
    stub.gate0_ready = lambda: (_ for _ in ()).throw(RuntimeError("config unreadable"))
    assert safety_state.gate0_shipped() is False
    stub.gate0_ready = lambda: True
    assert safety_state.gate0_shipped() is True


def test_an_editor_only_keyword_tag_survives_the_ais_different_tags(db, tax, cfg, tmp_path):
    story(
        db,
        "L",
        "Leader says metro will reach airport",
        fetched=NOW - H,
        topics=("O02",),
        sources=("S011", "S034", "S063"),
    )
    checked(db, "L")  # the AI confirmed O02 ...
    db.execute("DELETE FROM item_topics WHERE cluster_id = 'L' AND match_method = 'llm'")
    db.execute(
        "INSERT INTO item_topics (cluster_id, topic_id, match_method, tagged_at) VALUES ('L', 'D05', 'llm', ?)",
        (utc_iso(NOW),),
    )
    db.commit()  # ... no: it chose a generic metro topic instead
    stories = {s.story_id: s for s in load_stories(db, queries.window_for(NOW))}
    assert stories["L"].topic_ids == ["D05"] and "O02" in stories["L"].all_topic_ids
    assert safety_state.safety_state(stories["L"], tax.categories["Bangalore"], tax, gate0=True) == "editor_only"


def test_headlines_cannot_smuggle_links_or_markup_into_markdown():
    out = _link(r"a\](http://evil.example/x) b", "http://ok.example/")
    assert "(http://evil.example" not in out.replace("[.]", ".") or "\\]" in out or "evil[.]example" in out
    assert "http://evil.example" not in out
    out = _link("&lt;img src=x onerror=alert(1)&gt; https://evil.example **b**", "https://ok.example/")
    assert "https://evil.example" not in out and "**b**" not in out
    assert _link("Plain headline", "javascript:alert(1)") == "Plain headline"  # only http(s) links
    assert _link("Plain headline", "") == "Plain headline"
    assert _link("Metro fares", "https://ok.example/a b").endswith("(https://ok.example/a%20b)")


def test_a_reason_from_the_ai_cannot_make_a_link_in_the_report(db, tax, cfg, tmp_path):
    day(db)
    add_exclusion(db, "m3", reason="[click](http://evil.example/z)")
    r, folder = run(db, tax, cfg, tmp_path)
    picks_out.write_picks(r, folder, tax, cfg)
    report = (folder / "filter_report.md").read_text(encoding="utf-8")
    assert "http://evil.example" not in report
    import re

    assert not re.search(r"(?<!\\)\]\(", report)  # no unescaped Markdown link


LANGUAGE = [
    "Kannada signboard rule: notices issued to 200 shops",
    "Local job quota bill returns to the cabinet agenda",
    "Hindi imposition row flares over bank exam papers",
    "Migrant workers union meets minister on housing",
]


def test_picks_json_is_selected_among_cleared_stories_only(db, tax, cfg, tmp_path):
    # four editor-only Bangalore-category stories outscore one cleared metro story; they must not use up
    # the category's four places in picks.json
    for i, topic in enumerate(("O14", "O15", "O18", "B21")):
        cid = f"e{i}"
        story(db, cid, LANGUAGE[i], fetched=NOW - H, topics=(topic,), sources=("S011", "S034", "S063"))
        checked(db, cid)
    story(db, "ok", "Metro feeder buses start", fetched=NOW - 3 * H, topics=("D05",), sources=("S011", "S034"))
    checked(db, "ok")
    from riffi_ingest.taxonomy import CategoryRule

    three = Taxonomy({**tax.categories, "Bangalore": CategoryRule("Bangalore", 3, 3, 60, False)}, tax.needs, tax.topics)
    r, folder = run(db, three, cfg, tmp_path, gate0=True)
    editor = [a for a in r.selection.picks if a.safety_state == "editor_only"]
    assert len(editor) == 3 and "ok" not in {
        a.story.story_id for a in r.selection.picks
    }  # the editor's list is full ...
    assert [a.story.story_id for a in r.export.picks] == ["ok"]  # ... but picks.json still gets the cleared one
    picks_out.write_picks(r, folder, three, cfg)
    exported = json.loads((folder / "picks.json").read_text(encoding="utf-8"))["picks"]
    assert [p["story_id"] for p in exported] == ["ok"] and exported[0]["rank"] == 1


def test_what_goes_to_the_seed_agent_is_one_plain_line_with_web_links_only(db, tax, cfg, tmp_path):
    story(
        db,
        "k",
        "ಬೆಂಗಳೂರು ಮೆಟ್ರೋ ದರ ಏರಿಕೆ " + "x" * 600 + "\x00\x07\nsecond line",
        fetched=NOW - H,
        topics=("D05",),
        sources=("S011", "S034", "S063"),
        url="file:///etc/passwd",
    )
    checked(db, "k")
    r, folder = run(db, tax, cfg, tmp_path, gate0=True)
    picks_out.write_picks(r, folder, tax, cfg)
    row = json.loads((folder / "picks.json").read_text(encoding="utf-8"))["picks"][0]
    assert "\n" not in row["headline"] and "\x00" not in row["headline"] and len(row["headline"]) <= 300
    assert row["headline"].startswith("ಬೆಂಗಳೂರು") and "file:" not in row["links"]


def test_an_empty_window_gives_empty_files_not_a_crash(db, tax, cfg, tmp_path):
    r, folder = run(db, tax, cfg, tmp_path)
    picks_out.write_picks(r, folder, tax, cfg)
    assert "No story passed every gate" in (folder / "picks.md").read_text(encoding="utf-8")
    assert json.loads((folder / "picks.json").read_text(encoding="utf-8"))["picks"] == []


def test_a_window_over_the_story_cap_is_refused_not_cut(db, monkeypatch):
    day(db)
    monkeypatch.setattr("riffi_ingest.filter.model.STORY_CAP", 3)
    with pytest.raises(ValueError, match="more than 3 stories"):
        load_stories(db, queries.window_for(NOW))


def test_every_setting_the_filter_reads_is_checked_up_front(tmp_path):
    import yaml

    good = yaml.safe_load(Paths().filter.read_text(encoding="utf-8"))
    for section, key in (
        ("relevance", "ai_min"),
        ("content", "hook_max"),
        ("prefilter", "max_stories"),
        ("content", "sources_2"),
    ):
        broken = json.loads(json.dumps(good))
        del broken[section][key]
        path = tmp_path / "f.yaml"
        path.write_text(yaml.safe_dump(broken), encoding="utf-8")
        with pytest.raises(ValueError, match=key):
            FilterConfig.load(path)
