"""The editor's ground-truth log: the recall test scores every source against it, so each mistake a person
can make in Excel must be caught by check-log, with the row number, on the day it is made."""

from datetime import datetime, timezone

from typer.testing import CliRunner

from riffi_ingest import cli
from riffi_ingest.groundtruth import COLUMNS, Topic, load_topics, read_log, write_template

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)  # 17:30 IST
TOPICS = {
    "P01": Topic("P01", "Tunnel road", "", "High"),
    "D01": Topic("D01", "India vs New Zealand", "", "Low"),
}
HEADER = ",".join(COLUMNS)


def log(tmp_path, *rows, header=HEADER, encoding="utf-8"):
    path = tmp_path / "ground_truth.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding=encoding)
    return path


def test_a_good_row_is_ready_to_score_in_ist(tmp_path):
    check = read_log(log(tmp_path, "2026-10-12,P01,BBMP floats tunnel tender,X,09:15"), TOPICS, NOW)
    assert check.errors == [] and check.warnings == []
    (e,) = check.entries
    assert (e.row, e.topic_id, e.where_seen) == (2, "P01", "X")
    assert e.seen_at.isoformat() == "2026-10-12T09:15:00+05:30"


def test_what_excel_does_to_dates_and_times_still_reads(tmp_path):
    path = log(
        tmp_path,
        "12-10-2026,p01,one,TV,9:15 am",
        "12/10/2026,P01,two,TV,09:15:00",
        "2026-10-12,P01,three,TV,2:05 PM",
        ",,,,",  # Excel leaves blank rows behind
        header="Date,Topic ID,What happened,Where seen,Time seen,notes",
    )
    check = read_log(path, TOPICS, NOW)
    assert check.errors == []
    assert [e.seen_at.strftime("%d %H:%M") for e in check.entries] == ["12 09:15", "12 09:15", "12 14:05"]
    assert [e.topic_id for e in check.entries] == ["P01"] * 3


def test_each_mistake_is_named_with_its_row(tmp_path):
    path = log(
        tmp_path,
        "2026-10-12,P01,fine,X,08:00",
        "12th Oct,P01,bad date,X,08:00",
        "2026-10-12,P99,unknown topic,X,08:00",
        "2026-10-12,P01,bad time,X,8 o'clock",
        "2026-10-12,P01,,X,08:00",
        "2026-10-12,P01,from the future,X,23:00",
    )
    check = read_log(path, TOPICS, NOW)
    assert [e.row for e in check.entries] == [2]
    joined = "\n".join(check.errors)
    for expected in ("row 3: date", "row 4: topic_id 'P99'", "row 5: time_seen", "row 6: 'what_happened'", "row 7:"):
        assert expected in joined
    assert "future" in check.errors[-1]


def test_repeats_and_other_priorities_are_notes_not_errors(tmp_path):
    path = log(
        tmp_path,
        "2026-10-12,P01,Tunnel tender floated,X,08:00",
        "2026-10-12,P01,tunnel  tender FLOATED,TV,09:00",
        "2026-10-12,D01,Squad named,X,08:00",
    )
    check = read_log(path, TOPICS, NOW)
    assert check.errors == []
    assert [e.row for e in check.entries] == [2, 4]
    assert "row 3: the same development as row 2" in check.warnings[0]
    assert "row 4: D01 is a Low-priority topic" in check.warnings[1]


def test_a_wrong_header_or_encoding_stops_with_how_to_fix_it(tmp_path):
    assert (
        "missing column(s) time_seen"
        in read_log(log(tmp_path, header="date,topic_id,what_happened,where_seen"), TOPICS, NOW).errors[0]
    )
    path = log(tmp_path, "2026-10-12,P01,ಮೆಟ್ರೋ ದರ,X,08:00", encoding="utf-16")
    assert "CSV UTF-8" in read_log(path, TOPICS, NOW).errors[0]


def test_the_template_never_overwrites_the_editors_log(tmp_path, repo_root):
    topics = load_topics(repo_root / "topics.csv")
    log_path, list_path = tmp_path / "data" / "ground_truth.csv", tmp_path / "data" / "topics.csv"
    assert write_template(log_path, list_path, topics) is True
    assert log_path.read_text(encoding="utf-8-sig").strip() == HEADER
    listed = list_path.read_text(encoding="utf-8-sig").splitlines()
    assert listed[0] == "topic_id,topic,date_or_window"
    assert len(listed) - 1 == sum(t.priority == "High" for t in topics.values()) > 0
    log_path.write_text(HEADER + "\n2026-10-12,P01,kept,X,08:00\n", encoding="utf-8")
    assert write_template(log_path, list_path, topics) is False
    assert "kept" in log_path.read_text(encoding="utf-8")


def test_cli_creates_then_checks_the_log(tmp_path, repo_root):
    runner = CliRunner()
    path, topics = tmp_path / "gt.csv", str(repo_root / "topics.csv")
    r = runner.invoke(cli.app, ["check-log", "--log", str(path), "--topics", topics])
    assert r.exit_code == 1 and "log-template" in r.output
    r = runner.invoke(
        cli.app, ["log-template", "--log", str(path), "--topic-list", str(tmp_path / "t.csv"), "--topics", topics]
    )
    assert r.exit_code == 0 and "created" in r.output
    high = next(t for t in load_topics(repo_root / "topics.csv").values() if t.priority == "High")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"2026-10-05,{high.topic_id},something real,X,08:00\n2026-10-05,ZZ9,typo,X,08:00\n")
    r = runner.invoke(cli.app, ["check-log", "--log", str(path), "--topics", topics])
    assert r.exit_code == 1
    assert "Fix: row 3: topic_id 'ZZ9'" in r.output
    assert "1 developments over 1 days are ready to score; 1 problem(s) to fix." in r.output
