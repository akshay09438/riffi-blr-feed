"""Step 8: the digest (digest.md, digest.csv) and the feed health report (health.md, sources_health.csv), D-012.

Stories are written straight into a temporary database, so each test controls exactly when every report was
fetched; the CLI tests run the real commands against it. data/ is never touched (tests/conftest.py)."""

import csv
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from typer.testing import CliRunner

from riffi_ingest import cli
from riffi_ingest.db import connect
from riffi_ingest.db.connection import utc_iso
from riffi_ingest.db.importers import import_sources, import_topics
from riffi_ingest.fetchers.parse import IST
from riffi_ingest.outputs import digest, health_report, queries
from riffi_ingest.pipeline import Paths
from riffi_ingest.scoring import Scorer

NOW = datetime(2026, 10, 6, 8, 40, tzinfo=timezone.utc)  # 14:10 IST
H = timedelta(hours=1)
D = timedelta(days=1)
TABLES = ("sources", "fetch_runs", "items", "story_clusters", "item_topics", "topics", "engine_runs")


@pytest.fixture
def db(tmp_path, repo_root):
    conn = connect(tmp_path / "engine.db")
    import_sources(conn, repo_root / "feeds.csv", now=NOW - 3 * D)
    import_topics(conn, repo_root / "topics.csv", now=NOW - 3 * D)
    yield conn
    conn.close()


@pytest.fixture
def scorer():
    return Scorer.load(Paths().scoring)


def story(
    conn,
    cid,
    headline,
    *,
    fetched,
    score=50,
    label="Medium",
    topics=("O06",),
    method="keyword",
    sources=("S011",),
    sensitive=False,
    started=None,
    url=None,
):
    """One story with one report per source, all fetched at `fetched`, tagged with `topics` by `method`."""
    started = started or fetched - H
    conn.execute(
        "INSERT INTO story_clusters (cluster_id, headline, first_seen_at, started_at, source_count, sources,"
        " relevance_score, label, sensitive, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            cid,
            headline,
            utc_iso(fetched),
            utc_iso(started),
            len(sources),
            json.dumps(sorted(sources)),
            score,
            label,
            int(sensitive),
            utc_iso(fetched),
        ),
    )
    for n, sid in enumerate(sources):
        conn.execute(
            "INSERT INTO items (item_id, source_id, title, url, canonical_url, published_at, fetched_at, cluster_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"{cid}-{n}",
                sid,
                headline,
                url or f"https://example.in/{cid}/{n}",
                f"https://example.in/{cid}/{n}",
                utc_iso(started),
                utc_iso(fetched),
                cid,
            ),
        )
    for tid in topics:
        conn.execute(
            "INSERT INTO item_topics (cluster_id, topic_id, match_method, tagged_at) VALUES (?, ?, ?, ?)",
            (cid, tid, method, utc_iso(fetched)),
        )
    if method == "llm":
        ai_verdict(conn, cid)
    conn.commit()


def ai_verdict(conn, cid, whats_new=""):
    """What the AI pass stores for a story it has checked (tagging/llm_batches.py): it is then "AI checked"."""
    conn.execute(
        "INSERT INTO ai_verdicts (cluster_id, is_new_development, whats_new, debate_angle, excluded, excluded_reason,"
        " reports_seen, batch_id, batch_written_at, checked_at) VALUES (?, ?, ?, '', 0, '', 1, 'b', ?, ?)",
        (cid, int(bool(whats_new)), whats_new, utc_iso(NOW), utc_iso(NOW)),
    )


def window(now=NOW, day=None):
    return queries.window_for(now, day)


def build(conn, scorer, top=30, w=None):
    w = w or window()
    c = queries.counts(conn, w)
    stories = digest.load_stories(conn, w, scorer)
    text = digest.markdown(
        w,
        c,
        stories,
        queries.topics_moved(conn, w),
        queries.quiet_high_topics(conn, w.end),
        queries.history_start(conn),
        top=top,
        scorer=scorer,
    )
    return text, stories, c


# ---- the window


def test_the_default_window_is_the_24_hours_before_now_filed_under_todays_ist_date():
    w = window(datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc))  # 01:30 IST on 6 Oct
    assert (w.start, w.end) == (datetime(2026, 10, 4, 20, 0, tzinfo=timezone.utc), w.start + D)
    assert w.day == date(2026, 10, 6)


def test_a_date_means_the_24_hours_ending_0700_ist_that_day():
    w = window(day=date(2026, 10, 5))
    assert w.end == datetime(2026, 10, 5, 7, 0, tzinfo=IST) == datetime(2026, 10, 5, 1, 30, tzinfo=timezone.utc)
    assert w.start == w.end - D and w.day == date(2026, 10, 5)


def test_window_edges_start_is_in_end_is_out(db, scorer):
    w = window(day=date(2026, 10, 5))
    story(db, "c-start", "Fetched at the very start", fetched=w.start)
    story(db, "c-end", "Fetched at 07:00 IST, belongs to the next day", fetched=w.end)
    story(db, "c-before", "Fetched a second too early", fetched=w.start - timedelta(seconds=1))
    _, stories, c = build(db, scorer, w=w)
    assert [s.headline for s in stories] == ["Fetched at the very start"]
    assert c.items == 1 and c.stories == 1


def test_a_story_counts_when_any_report_was_fetched_in_the_window(db, scorer):
    story(db, "old", "Began two days ago, grew today", fetched=NOW - 2 * D)
    db.execute(
        "INSERT INTO items (item_id, source_id, title, url, canonical_url, published_at, fetched_at, cluster_id)"
        " VALUES ('old-new', 'S034', 't', 'https://e.in/x', 'https://e.in/x', ?, ?, 'old')",
        (utc_iso(NOW - 2 * H), utc_iso(NOW - 2 * H)),
    )
    db.commit()
    _, stories, c = build(db, scorer)
    assert [s.headline for s in stories] == ["Began two days ago, grew today"]
    assert c.stories == 1 and c.new_stories == 0 and c.items == 1


# ---- ranking, labels, flags


def test_stories_are_ranked_and_drop_is_counted_never_listed(db, scorer):
    story(db, "a", "Second best", fetched=NOW - 2 * H, score=60, sources=("S011",))
    story(db, "b", "Best", fetched=NOW - 3 * H, score=75, label="High", sources=("S011", "S034"))
    story(db, "c", "Tie on score, more sources", fetched=NOW - 4 * H, score=60, sources=("S011", "S034"))
    story(db, "d", "Excluded flashpoint", fetched=NOW - 1 * H, score=0, label="Drop")
    text, stories, c = build(db, scorer)
    assert [s.headline for s in stories] == ["Best", "Tie on score, more sources", "Second best"]
    assert c.labels == {"High": 1, "Medium": 2, "Drop": 1} and c.stories == 4
    assert "Excluded flashpoint" not in text
    assert "1 High / 2 Medium / 0 Low / 1 Drop" in text
    assert "The best 3 of 3 stories" in text


def test_awaiting_ai_marker_and_the_ai_tag_rule(db, scorer):
    story(db, "kw", "Keyword only", fetched=NOW - H, score=55)
    story(db, "ai", "AI checked", fetched=NOW - H, score=50, topics=("O06",))
    db.execute(
        "INSERT INTO item_topics (cluster_id, topic_id, match_method, tagged_at) VALUES ('ai', 'D05', 'llm', ?)",
        (utc_iso(NOW),),
    )
    ai_verdict(db, "ai")
    db.commit()
    text, stories, c = build(db, scorer)
    by = {s.headline: s for s in stories}
    assert not by["Keyword only"].ai_checked and by["AI checked"].ai_checked
    assert by["AI checked"].topics == ["D05"]  # the AI's topics win over the keyword pass's, as in scoring
    assert c.awaiting_ai == 1
    assert "AI pass: not run for 1 of 2 stories. Their scores leave out up to 25 points" in text
    rows = {s.headline: digest.csv_row(s) for s in stories}
    assert rows["Keyword only"]["whats_new"] == "awaiting AI pass" and rows["Keyword only"]["ai_checked"] == "no"
    assert rows["AI checked"]["whats_new"] == "" and rows["AI checked"]["ai_checked"] == "yes"
    line = next(line for line in text.splitlines() if "AI checked" in line)
    assert "awaiting AI" not in line


def test_when_every_story_is_ai_checked_the_marker_is_gone(db, scorer):
    story(db, "ai", "AI checked", fetched=NOW - H, method="llm")
    text, _, _ = build(db, scorer)
    assert "AI pass: run for all 1 stories." in text and "awaiting AI" not in text


def test_sensitive_stories_are_flagged(db, scorer):
    story(db, "s", "Stray dog feeding row", fetched=NOW - H, topics=("B10",), sensitive=True)
    text, stories, _ = build(db, scorer)
    assert stories[0].sensitive and digest.csv_row(stories[0])["sensitive"] == "yes"
    assert "| sensitive, awaiting AI |" in text


def test_the_topic_angle_is_shown_and_marked_as_not_this_storys(db, scorer):
    # D01 is Low priority Pan-India, O06 High Bangalore: the story scores by O06, so O06's angle is shown
    story(db, "t", "Tunnel road tender floated", fetched=NOW - H, topics=("D01", "O06"))
    text, stories, _ = build(db, scorer)
    s = stories[0]
    assert s.angle_topic == "O06" and s.angle.startswith("Tunnels for cars vs metro for people")
    row = digest.csv_row(s)
    assert row["debate_angle"].startswith("topic angle, not this story (O06): Tunnels for cars")
    assert "topic angle, not this story (O06): Tunnels for cars" in text


def test_top_limits_the_markdown_and_the_cap_limits_the_csv(db, scorer, monkeypatch):
    for n in range(5):
        story(db, f"c{n}", f"Story {n}", fetched=NOW - H, score=40 + n)
    text, _, _ = build(db, scorer, top=2)
    table = [line for line in text.splitlines() if line.startswith("| ") and "Story" in line]
    assert [line.split("|")[1].strip() for line in table] == ["1", "2"]
    monkeypatch.setattr(digest, "CSV_CAP", 3)
    assert len(digest.load_stories(db, window(), scorer)) == 3


def test_kannada_and_pipes_survive(db, scorer, tmp_path):
    story(db, "kn", "ಬೆಂಗಳೂರು ಸುರಂಗ ರಸ್ತೆ | ಟೆಂಡರ್", fetched=NOW - H, url="https://e.in/a|b")
    result = digest.write_digest(db, window(), tmp_path / "out", top=30, scorer=scorer)
    text = result.md_path.read_text(encoding="utf-8")
    assert "ಬೆಂಗಳೂರು ಸುರಂಗ ರಸ್ತೆ \\| ಟೆಂಡರ್" in text and "https://e.in/a%7Cb" in text
    assert result.csv_path.read_bytes().startswith(b"\xef\xbb\xbf")  # utf-8-sig: Excel shows Kannada
    with open(result.csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["headline"] == "ಬೆಂಗಳೂರು ಸುರಂಗ ರಸ್ತೆ | ಟೆಂಡರ್" and rows[0]["url"] == "https://e.in/a|b"
    assert list(rows[0]) == list(digest.CSV_FIELDS)
    # every Markdown table row still has its 9 cells (the angles in topics.csv contain `|` too)
    for line in text.splitlines():
        if line.startswith("| 1 |"):
            assert len(line.replace("\\|", "").split("|")) == 11


# ---- topics that moved, topics that went quiet, counts


def test_topics_with_new_developments(db, scorer):
    story(db, "a", "Tunnel road tender floated", fetched=NOW - H, score=70, label="High")
    story(db, "b", "Tunnel road protest", fetched=NOW - H, score=50)
    story(db, "c", "Cricket on office time", fetched=NOW - H, score=30, label="Low", topics=("D01",))
    story(db, "d", "Dropped story", fetched=NOW - H, score=5, label="Drop", topics=("P01",))
    rows = queries.topics_moved(db, window())
    assert [(r["topic_id"], r["stories"], r["headline"]) for r in rows] == [
        ("O06", 2, "Tunnel road tender floated"),
        ("D01", 1, "Cricket on office time"),
    ]
    text, _, _ = build(db, scorer)
    assert "- **O06**" in text and "2 stories. Best: Tunnel road tender floated" in text and "**P01**" not in text


def test_a_high_topic_quiet_for_8_days_is_listed_and_one_seen_2_days_ago_is_not(db, scorer):
    story(db, "old", "Tunnel road, a while ago", fetched=NOW - 8 * D, started=NOW - 8 * D)
    story(db, "new", "Metro Pink Line inspection", fetched=NOW - 2 * D, started=NOW - 2 * D, topics=("D05",))
    db.execute("INSERT INTO engine_runs (started_at, mode, outcome) VALUES (?, 'all', 'ok')", (utc_iso(NOW - 9 * D),))
    db.commit()
    quiet = {r["topic_id"]: r["last_seen"] for r in queries.quiet_high_topics(db, NOW)}
    assert quiet["O06"] == utc_iso(NOW - 8 * D) and "D05" not in quiet
    assert quiet["P01"] is None  # a High topic never seen at all
    text, _, _ = build(db, scorer)
    assert "- **O06**" in text.split("## High-priority topics")[1]
    assert "last story began 28 Sep 2026" in text and "History starts" not in text


def test_a_short_history_is_said_plainly(db, scorer):
    story(db, "a", "First ever story", fetched=NOW - 2 * D)
    text, _, _ = build(db, scorer)
    assert "History starts 4 Oct 2026, less than 7 days before this digest" in text


def test_counts(db, scorer):
    story(db, "a", "New today, two reports", fetched=NOW - H, sources=("S011", "S034"), label="High", score=72)
    story(db, "b", "Grew today", fetched=NOW - 3 * D, label="Low", score=25)
    db.execute(
        "INSERT INTO items (item_id, source_id, title, url, canonical_url, published_at, fetched_at, cluster_id)"
        " VALUES ('b-new', 'S034', 't', 'https://e.in/b', 'https://e.in/b', ?, ?, 'b')",
        (utc_iso(NOW - H), utc_iso(NOW - H)),
    )
    db.commit()
    c = queries.counts(db, window())
    assert (c.items, c.stories, c.new_stories, c.awaiting_ai) == (3, 2, 1, 2)
    assert c.labels == {"High": 1, "Low": 1}
    text, _, _ = build(db, scorer)
    assert "Counts: 3 items fetched, 2 stories (1 new), 1 High / 0 Medium / 1 Low / 0 Drop." in text


def test_an_empty_window_says_what_to_do(db, scorer):
    story(db, "a", "Long ago", fetched=NOW - 3 * D)
    text, _, _ = build(db, scorer)
    assert "No articles were fetched in this window" in text and "## Top stories" not in text


# ---- the health report


def set_health(conn, sid, **cols):
    conn.execute(
        "UPDATE sources SET " + ", ".join(f"{k} = ?" for k in cols) + " WHERE source_id = ?", (*cols.values(), sid)
    )
    conn.commit()


def test_health_categories(db):
    ok = {"last_status": "ok", "last_ok_at": utc_iso(NOW - H)}
    set_health(db, "S011", **ok, newest_item_at=utc_iso(NOW - 2 * H), fields_present="title,link,date")
    set_health(db, "S034", last_status="error: HTTP 404", consecutive_failures=4)
    set_health(db, "S001", last_status="error: timeout", consecutive_failures=1)
    set_health(db, "S002", **ok, newest_item_at=utc_iso(NOW - 9 * D))  # a feed quiet for 9 days
    set_health(db, "S029", **ok, newest_item_at=utc_iso(NOW - 9 * D))  # a page monitor: 9 days is fine
    set_health(db, "S047", last_status="skipped: fetch_url is not a URL")
    story(db, "a", "Tagged story", fetched=NOW - H, sources=("S011",))
    states = {s.row["source_id"]: s for s in health_report.assess(db, NOW)}
    assert states["S011"].category == "working" and states["S011"].useful
    assert states["S034"].category == "failing" and "4 in a row" in states["S034"].detail
    assert states["S001"].category == "failed"
    assert states["S002"].category == "stale" and "9 days ago" in states["S002"].detail
    assert states["S029"].category == "working" and not states["S029"].useful  # nothing tagged in 7 days
    assert states["S047"].category == "skipped"
    assert states["S112"].category == "manual"
    assert states["S004"].category == "never"


def test_health_markdown_and_csv(db, tmp_path):
    set_health(
        db,
        "S011",
        last_status="ok",
        last_ok_at="2026-10-06T08:00:00+00:00",
        newest_item_at="2026-10-06T07:30:00+00:00",
        fields_present="title,link,date,summary",
    )
    set_health(db, "S034", last_status="error: HTTP 404", consecutive_failures=3)
    result = health_report.write_health(
        db,
        tmp_path,
        now=NOW,
        day=date(2026, 10, 6),
        engine_lines=["Last run: fine.", "  S034 indented line"],
        blocklist_problems=["blocklist.csv row 9: no domain in 'Khel Now' - add the site's domain"],
    )
    text = result.md_path.read_text(encoding="utf-8")
    assert text.startswith("# Riffi feed health, 6 Oct 2026 (as of 06 Oct 14:10 IST)")
    assert "- Last run: fine.\n  - S034 indented line" in text
    failing = text.split("### Failing 3+ runs in a row")[1].split("###")[0]
    assert "S034 ESPNcricinfo - 3 in a row - error: HTTP 404" in failing
    assert "row 9: no domain in 'Khel Now'" in text and "Passing health checks: 1 of" in text
    assert "History starts" in text  # under 7 days of history, "not useful" says so
    with open(result.csv_path, encoding="utf-8-sig", newline="") as f:
        lines = list(csv.reader(f))
    assert lines[0] == ["source_id", "name", "last_status", "last_ok_at", "newest_item_at", "fields_present"]
    assert lines[1][0] == "S001" and [r[0] for r in lines[1:]] == sorted(r[0] for r in lines[1:])
    s011 = next(r for r in lines if r[0] == "S011")
    assert s011[2:] == ["ok", "2026-10-06 13:30", "2026-10-06 13:00", "title,link,date,summary"]  # IST times
    assert len(lines) - 1 == db.execute("SELECT COUNT(*) FROM sources WHERE active = 1").fetchone()[0]


def test_a_past_day_says_health_is_as_of_now(db, tmp_path):
    result = health_report.write_health(
        db, tmp_path, now=NOW, day=date(2026, 10, 3), engine_lines=[], blocklist_problems=[]
    )
    assert "not as of 3 Oct 2026" in result.md_path.read_text(encoding="utf-8")


# ---- the commands


def row_counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def invoke(*args):
    return CliRunner().invoke(cli.app, [str(a) for a in args])


def test_digest_writes_four_files_overwrites_on_rerun_and_changes_no_row(db, tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    story(db, "a", "Tunnel road tender floated", fetched=now - H, score=72, label="High")
    before = row_counts(db)
    out = tmp_path / "reports"
    r = invoke("digest", "--db", tmp_path / "engine.db", "--out", out)
    assert r.exit_code == 0, r.output
    folder = out / now.astimezone(IST).date().isoformat()
    names = {"digest.md", "digest.csv", "health.md", "sources_health.csv"}
    assert {p.name for p in folder.iterdir()} == names
    assert "1 High" in r.output and "Health:" in r.output
    assert "Tunnel road tender floated" in (folder / "digest.md").read_text(encoding="utf-8")
    (folder / "digest.md").write_text("stale copy", encoding="utf-8")
    assert invoke("digest", "--db", tmp_path / "engine.db", "--out", out, "--top", 5).exit_code == 0
    assert (folder / "digest.md").read_text(encoding="utf-8").startswith("# Riffi digest")
    assert row_counts(db) == before


def test_digest_for_a_past_date_goes_in_that_days_folder(db, tmp_path):
    story(db, "a", "Yesterday morning", fetched=datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc))
    out = tmp_path / "reports"
    r = invoke("digest", "--db", tmp_path / "engine.db", "--out", out, "--date", "2026-10-05")
    assert r.exit_code == 0, r.output
    assert "Yesterday morning" in (out / "2026-10-05" / "digest.md").read_text(encoding="utf-8")
    assert "(04 Oct 07:00 to 05 Oct 07:00 IST)" in (out / "2026-10-05" / "digest.md").read_text(encoding="utf-8")


def test_bad_dates_and_tops_are_refused(db, tmp_path):
    story(db, "a", "x", fetched=NOW)
    base = ("digest", "--db", tmp_path / "engine.db", "--out", tmp_path / "r")
    r = invoke(*base, "--date", "05-10-2026")
    assert r.exit_code == 1 and "write it as YYYY-MM-DD" in r.output
    r = invoke(*base, "--date", "2099-01-01")
    assert r.exit_code == 1 and "in the future" in r.output
    r = invoke(*base, "--top", 0)
    assert r.exit_code == 1 and "--top must be between 1 and 2000" in r.output
    assert not (tmp_path / "r").exists()


def test_an_empty_or_missing_database_gives_a_clear_message(tmp_path):
    empty = tmp_path / "empty.db"
    connect(empty).close()
    r = invoke("digest", "--db", empty, "--out", tmp_path / "r")
    assert r.exit_code == 1 and "no articles yet" in r.output and "fetch --all" in r.output
    r = invoke("report", "--db", empty, "--out", tmp_path / "r")
    assert r.exit_code == 1 and "no sources yet" in r.output
    missing = tmp_path / "missing.db"
    r = invoke("digest", "--db", missing, "--out", tmp_path / "r")
    assert r.exit_code == 1 and "no database" in r.output and not missing.exists()


def test_report_writes_only_the_health_files(db, tmp_path):
    out = tmp_path / "reports"
    r = invoke("report", "--db", tmp_path / "engine.db", "--out", out, "--date", "2026-10-01")
    assert r.exit_code == 0, r.output
    assert {p.name for p in (out / "2026-10-01").iterdir()} == {"health.md", "sources_health.csv"}
    text = (out / "2026-10-01" / "health.md").read_text(encoding="utf-8")
    assert "## Engine" in text and "No runs in the run diary yet" in text and "## Blocklist" in text


def test_the_engine_section_does_not_repeat_the_failing_sources():
    from riffi_ingest.outputs.health_report import _engine_section

    lines = _engine_section(
        ["Last run: ok.", "Sources failing 3+ runs in a row (1; ...):", "  S057 Vijaya Karnataka - 3", "Warning: x"]
    )
    assert "- Last run: ok." in lines and "- Warning: x" in lines
    assert not any("S057" in line or "Sources failing" in line for line in lines)
