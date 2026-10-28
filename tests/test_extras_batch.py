"""`extras-batch`: batches for the prefiltered top stories, already AI-judged ones included, so that the AI pass's
extra judgements (why the reader cares, the two sides, a hook) reach every story the picks need. The allow-list of
which stories a batch may name lives in a record outside the batch folder, and `ai-facts` accepts those folders
with it. (28 Oct 2026, from the first real run.)"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from test_picks import NOW, H, add_exclusion, checked, story
from test_picks_round2 import FULL
from typer.testing import CliRunner

from riffi_ingest import cli
from riffi_ingest.db import connect
from riffi_ingest.db.importers import import_sources, import_topics
from riffi_ingest.filter.config import FilterConfig
from riffi_ingest.filter.extras_batch import write_extras_batches
from riffi_ingest.filter.facts import load_facts
from riffi_ingest.filter.facts_apply import apply_extras
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


def seed(db, n_metro=6):
    for i in range(n_metro):
        story(
            db,
            f"m{i}",
            f"Metro story number {i} about a different thing {i * 7}",
            fetched=NOW - H,
            topics=("D05",),
            sources=("S011", "S034", "S063"),
        )
    story(
        db,
        "h1",
        "Gym memberships get costlier across Indian cities",
        fetched=NOW - H,
        topics=("P56",),
        sources=("S011", "S034"),
    )
    story(
        db,
        "old",
        "Old metro story from last week",
        fetched=NOW - H,
        started=NOW - 6 * 24 * H,
        topics=("D05",),
        sources=("S011", "S034"),
    )
    story(db, "x", "Row over a religious festival", fetched=NOW - H, topics=("D05",), sources=("S011", "S034"))
    add_exclusion(db, "x")
    checked(db, "m0", whats_new="Fares rise", angle="Is it fair?")  # already judged by the AI pass today


def run_extras(db, tax, cfg, tmp_path, **kw):
    return write_extras_batches(
        db,
        queries.window_for(NOW),
        tax,
        cfg,
        tmp_path / "ai",
        tmp_path / "records",
        now=NOW,
        sidecar=tmp_path / "side.json",
        **kw,
    )


def names(run):
    out = []
    for p in run.batches:
        out += [s["story_id"] for s in json.loads(p.read_text(encoding="utf-8"))["stories"]]
    return out


def test_batches_hold_fresh_non_excluded_stories_including_the_already_judged(db, tax, cfg, tmp_path):
    seed(db)
    run = run_extras(db, tax, cfg, tmp_path)
    got = names(run)
    assert "m0" in got and "h1" in got  # judged and unjudged alike
    assert "x" not in got and "old" not in got  # excluded stories and stories older than 72 h are never sent
    assert run.folder.name.endswith("-extras") and (run.folder / "topics.json").exists()
    batch = json.loads(run.batches[0].read_text(encoding="utf-8"))
    assert batch["kind"] == "extras" and batch["extra_file"] == "extra-01.json" and "answer_file" not in batch
    story0 = batch["stories"][0]
    assert {"story_id", "headline", "reports", "category", "topics", "editor_only"} <= set(story0)
    m0 = next(
        s for p in run.batches for s in json.loads(p.read_text(encoding="utf-8"))["stories"] if s["story_id"] == "m0"
    )
    assert m0["ai_debate_angle"] == "Is it fair?"  # what the AI pass already said is shown, not asked again


def test_batches_hold_at_most_twenty_stories_and_the_cap_is_honoured(db, tax, cfg, tmp_path):
    for i in range(45):
        story(
            db,
            f"z{i:02d}",
            f"Distinct headline {i} about subject {i * 13} and place {i * 17}",
            fetched=NOW - H,
            topics=("O13",),
            sources=("S011", "S034"),
        )
    run = run_extras(db, tax, cfg, tmp_path, max_stories=30)
    assert run.stories == 30 and len(run.batches) == 2
    sizes = [len(json.loads(p.read_text(encoding="utf-8"))["stories"]) for p in run.batches]
    assert sizes == [20, 10]


def test_every_category_gets_its_minimum_before_the_budget_runs_out(db, tax, cfg, tmp_path):
    for i in range(40):
        story(
            db,
            f"b{i:02d}",
            f"Bengaluru item {i} concerning thing {i * 11} and spot {i * 19}",
            fetched=NOW - H,
            topics=("D05",),
            sources=("S011", "S034", "S063"),
        )
    for i in range(12):
        story(
            db,
            f"f{i:02d}",
            f"Gym trend {i} regarding item {i * 23} and brand {i * 29}",
            fetched=NOW - H,
            topics=("P56",),
            sources=("S011",),
        )
    small = FilterConfig({**cfg.raw, "prefilter": {**cfg["prefilter"], "max_stories": 30, "min_per_category": 10}})
    run = run_extras(db, tax, small, tmp_path)
    health = [s for s in names(run) if s.startswith("f")]
    assert len(health) >= 10 and run.per_category["Health & fitness"] >= 10 and run.stories <= 30


def test_the_best_batches_come_first(db, tax, cfg, tmp_path):
    seed(db)
    run = run_extras(db, tax, cfg, tmp_path)
    first = json.loads(run.batches[0].read_text(encoding="utf-8"))["stories"]
    assert first[0]["story_id"] != "h1"  # a Bangalore High story outranks the Pan-India Medium one


def test_stories_that_already_have_extras_are_not_sent_again_unless_asked(db, tax, cfg, tmp_path):
    seed(db)
    (tmp_path / "side.json").write_text(json.dumps({"m1": FULL}), encoding="utf-8")
    assert "m1" not in names(run_extras(db, tax, cfg, tmp_path))
    assert "m1" in names(run_extras(db, tax, cfg, tmp_path, again=True))


def test_nothing_is_written_when_nothing_qualifies(db, tax, cfg, tmp_path):
    run = run_extras(db, tax, cfg, tmp_path)
    assert run.stories == 0 and run.folder is None and not (tmp_path / "ai").exists()


# ---- ai-facts accepts these folders through the record


def write_extra(run, n, entries, batch_id=None):
    batch = json.loads(run.batches[n - 1].read_text(encoding="utf-8"))
    path = run.folder / batch["extra_file"]
    path.write_text(json.dumps({"batch_id": batch_id or batch["batch_id"], "extras": entries}), encoding="utf-8")


def test_ai_facts_stores_extras_for_an_extras_folder(db, tax, cfg, tmp_path):
    seed(db)
    run = run_extras(db, tax, cfg, tmp_path)
    write_extra(run, 1, [{"story_id": "m0", **FULL}, {"story_id": "h1", "daily_life": 9}])
    r = apply_extras(db, run.folder, tmp_path / "side.json", records_dir=tmp_path / "records")
    assert r.applied == 2 and not r.rejected
    facts, _ = load_facts(tmp_path / "side.json")
    assert facts["m0"].two_sided and facts["h1"].daily_life == 9


def test_the_record_is_the_allow_list(db, tax, cfg, tmp_path):
    seed(db)
    run = run_extras(db, tax, cfg, tmp_path)
    write_extra(run, 1, [{"story_id": "old", **FULL}, {"story_id": "never-heard-of-it"}])
    r = apply_extras(db, run.folder, tmp_path / "side.json", records_dir=tmp_path / "records")
    assert r.applied == 0 and {why for _, _, why in r.rejected} == {"not a story of this batch"}
    write_extra(run, 1, [{"story_id": "m0"}], batch_id="another-batch")
    r = apply_extras(db, run.folder, tmp_path / "side.json", records_dir=tmp_path / "records")
    assert r.rejected and "batch_id" in r.rejected[0][2]


def test_a_copied_folder_or_a_missing_record_is_refused(db, tax, cfg, tmp_path):
    seed(db)
    run = run_extras(db, tax, cfg, tmp_path)
    copy = tmp_path / "ai" / "copy-extras"
    copy.mkdir()
    (copy / "extra-01.json").write_text((run.folder / "batch-01.json").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(LookupError):
        apply_extras(db, copy, tmp_path / "side.json", records_dir=tmp_path / "records")  # no record for this name
    # a record copied under another name still names the original path
    (tmp_path / "records" / "copy-extras.json").write_text(
        (tmp_path / "records" / f"{run.folder.name}.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(LookupError):
        apply_extras(db, copy, tmp_path / "side.json", records_dir=tmp_path / "records")
    with pytest.raises(LookupError):
        apply_extras(db, run.folder, tmp_path / "side.json", records_dir=tmp_path / "nowhere")


def test_extras_for_a_story_excluded_since_are_skipped(db, tax, cfg, tmp_path):
    seed(db)
    run = run_extras(db, tax, cfg, tmp_path)
    add_exclusion(db, "m1")
    write_extra(run, 1, [{"story_id": "m1", **FULL}])
    r = apply_extras(db, run.folder, tmp_path / "side.json", records_dir=tmp_path / "records")
    assert r.applied == 0 and r.skipped_excluded == 1


def test_a_second_extras_batch_after_ai_facts_sends_only_what_is_left(db, tax, cfg, tmp_path):
    seed(db)
    first = run_extras(db, tax, cfg, tmp_path)
    sent = names(first)
    write_extra(first, 1, [{"story_id": sid, **FULL} for sid in sent[:3]])
    apply_extras(db, first.folder, tmp_path / "side.json", records_dir=tmp_path / "records")
    second = run_extras(db, tax, cfg, tmp_path)
    assert not set(sent[:3]) & set(names(second))


# ---- the commands


def test_extras_batch_and_ai_facts_commands_end_to_end(db, tmp_path):
    from test_picks import day

    day(db, datetime.now(timezone.utc))
    db_path = str(tmp_path / "engine.db")
    common = [
        "--db",
        db_path,
        "--out",
        str(tmp_path / "reports"),
        "--batches",
        str(tmp_path / "ai"),
        "--records",
        str(tmp_path / "records"),
    ]
    r = runner.invoke(cli.app, ["extras-batch", *common])
    assert r.exit_code == 0, r.output
    assert "stories in" in r.output and "Folder:" in r.output and "Bangalore" in r.output
    folder = next((tmp_path / "ai").iterdir())
    batch = json.loads(next(folder.glob("batch-*.json")).read_text(encoding="utf-8"))
    sid = batch["stories"][0]["story_id"]
    (folder / batch["extra_file"]).write_text(
        json.dumps({"batch_id": batch["batch_id"], "extras": [{"story_id": sid, **FULL}]}), encoding="utf-8"
    )
    r = runner.invoke(
        cli.app,
        [
            "ai-facts",
            str(folder),
            "--db",
            db_path,
            "--out",
            str(tmp_path / "reports"),
            "--records",
            str(tmp_path / "records"),
        ],
    )
    assert r.exit_code == 0 and "1 stored" in r.output, r.output
    r = runner.invoke(cli.app, ["extras-batch", *common])
    assert r.exit_code == 0
    again = [p for p in (tmp_path / "ai").iterdir() if p != folder]
    sent_again = [
        s["story_id"]
        for p in again
        for b in p.glob("batch-*.json")
        for s in json.loads(b.read_text(encoding="utf-8"))["stories"]
    ]
    assert sid not in sent_again


def test_extras_batch_says_so_when_nothing_qualifies(db, tmp_path):
    r = runner.invoke(
        cli.app,
        [
            "extras-batch",
            "--db",
            str(tmp_path / "engine.db"),
            "--out",
            str(tmp_path / "r"),
            "--batches",
            str(tmp_path / "ai"),
            "--records",
            str(tmp_path / "rec"),
        ],
    )
    assert r.exit_code != 0 or "Nothing" in r.output
