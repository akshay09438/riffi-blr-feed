import re
from datetime import datetime, timedelta, timezone

import pytest

from riffi_ingest.scheduler import Schedule, ScheduleError, due, parse_interval
from riffi_ingest.sources import Source, load_sources

NOW = datetime(2026, 10, 10, 6, 0, tzinfo=timezone.utc)
SPEEDS = {
    "Google News RSS": timedelta(hours=2),
    "RSSHub Telegram": timedelta(minutes=30),
    "Web page monitor": timedelta(hours=6),
    "Manual": None,
}
SCHEDULE = Schedule(by_route=SPEEDS, early=timedelta(minutes=5))


def src(source_id, route_type):
    return Source(source_id=source_id, name=source_id, category="", route_type=route_type, fetch_url="https://a.in/f")


GN, TG, PAGE = src("S001", "Google News RSS"), src("S002", "RSSHub Telegram"), src("S003", "Web page monitor")


def test_speeds_are_minutes_hours_or_never():
    assert parse_interval("30m") == timedelta(minutes=30)
    assert parse_interval("2h") == timedelta(hours=2)
    assert parse_interval(" 6H ") == timedelta(hours=6)
    assert parse_interval("never") is None
    for bad in ("", "2 hours", "0m", "-1h", "90s", "1.5h", "120"):
        with pytest.raises(ScheduleError):
            parse_interval(bad)


def test_a_source_never_tried_is_due():
    assert due([GN, TG, PAGE], {}, SCHEDULE, NOW) == [GN, TG, PAGE]


def test_each_route_type_has_its_own_speed():
    def last(ago):
        return {s.source_id: NOW - ago for s in (GN, TG, PAGE)}

    assert due([GN, TG, PAGE], last(timedelta(minutes=31)), SCHEDULE, NOW) == [TG]
    assert due([GN, TG, PAGE], last(timedelta(hours=2)), SCHEDULE, NOW) == [GN, TG]
    assert due([GN, TG, PAGE], last(timedelta(hours=6)), SCHEDULE, NOW) == [GN, TG, PAGE]


def test_early_minutes_absorb_timer_jitter():
    # the last run's clock started a few seconds after its tick; the next tick comes 30 minutes after that tick
    assert due([TG], {"S002": NOW - timedelta(minutes=29, seconds=55)}, SCHEDULE, NOW) == [TG]
    assert due([TG], {"S002": NOW - timedelta(minutes=25)}, SCHEDULE, NOW) == [TG]  # exactly interval - early
    assert due([TG], {"S002": NOW - timedelta(minutes=24, seconds=59)}, SCHEDULE, NOW) == []


def test_manual_and_route_types_without_a_speed_are_never_due():
    assert due([src("S100", "Manual"), src("S101", "Carrier pigeon")], {}, SCHEDULE, NOW) == []


def test_a_per_source_speed_beats_the_route_speed():
    faster = Schedule(by_route=SPEEDS, overrides={"S001": timedelta(minutes=30)}, early=timedelta(minutes=5))
    assert due([GN], {"S001": NOW - timedelta(minutes=30)}, faster, NOW) == [GN]
    off = Schedule(by_route=SPEEDS, overrides={"S001": None})
    assert due([GN], {}, off, NOW) == []


def test_load_reads_the_file_and_names_a_bad_value(tmp_path):
    path = tmp_path / "schedule.yaml"
    path.write_text(
        "early_minutes: 3\nby_route:\n  Google News RSS: 2h\n  Manual: never\nsources:\n  s016: 30m\n", encoding="utf-8"
    )
    loaded = Schedule.load(path)
    assert loaded.early == timedelta(minutes=3)
    assert loaded.by_route == {"Google News RSS": timedelta(hours=2), "Manual": None}
    assert loaded.overrides == {"S016": timedelta(minutes=30)}
    path.write_text("by_route:\n  Google News RSS: fortnightly\n", encoding="utf-8")
    with pytest.raises(ScheduleError, match="fortnightly"):
        Schedule.load(path)
    path.write_text("by_route:\n  Manual: never\nsources:\n  # S016: 30m\n", encoding="utf-8")
    assert Schedule.load(path).overrides == {}  # an overrides block holding only comments is empty


@pytest.mark.parametrize("bad", ["120", "30", "-1", "2.5", "five", "true"])
def test_early_minutes_must_be_a_whole_number_from_0_to_29(tmp_path, bad):
    path = tmp_path / "schedule.yaml"
    path.write_text(f"early_minutes: {bad}\nby_route:\n  Manual: never\n", encoding="utf-8")
    # the message names the setting and the value (the file's own path also contains "early_minutes" here)
    with pytest.raises(ScheduleError, match=rf"(?i)early_minutes must be .*0 to 29, not '?{re.escape(bad)}'?"):
        Schedule.load(path)


@pytest.mark.parametrize("good, minutes", [("0", 0), ("29", 29), ("'7'", 7)])
def test_early_minutes_inside_the_range_load(tmp_path, good, minutes):
    path = tmp_path / "schedule.yaml"
    path.write_text(f"early_minutes: {good}\nby_route:\n  Manual: never\n", encoding="utf-8")
    assert Schedule.load(path).early == timedelta(minutes=minutes)


def test_a_missing_early_minutes_defaults_to_5(tmp_path):
    path = tmp_path / "schedule.yaml"
    path.write_text("by_route:\n  Manual: never\n", encoding="utf-8")
    assert Schedule.load(path).early == timedelta(minutes=5)


def test_problems_lists_route_types_without_a_speed_and_unknown_overrides():
    schedule = Schedule(by_route={"Google News RSS": timedelta(hours=2)}, overrides={"S999": None})
    assert schedule.problems([GN, src("S004", "YouTube Atom")]) == [
        "no speed for route type 'YouTube Atom'",
        "a speed for unknown or inactive source S999",
    ]


def test_the_real_schedule_covers_every_real_source(repo_root):
    schedule = Schedule.load(repo_root / "config" / "schedule.yaml")
    sources = load_sources(repo_root / "feeds.csv")
    assert schedule.problems(sources) == []
    speeds = {s.route_type: schedule.interval(s) for s in sources}
    assert speeds == {  # D-008: the two-week test's speeds
        "Google News RSS": timedelta(hours=2),
        "X/Instagram via RSS.app": timedelta(hours=2),
        "Native publisher RSS/Atom": timedelta(hours=2),
        "YouTube Atom": timedelta(hours=2),
        "RSSHub Telegram": timedelta(minutes=30),
        "Web page monitor": timedelta(hours=6),
        "Manual": None,
    }
    assert schedule.early == timedelta(minutes=5)


def test_a_last_attempt_dated_in_the_future_is_due():
    # the clock was wrong when it was stamped (then corrected): fetch now rather than wait out the error
    last = {"S002": NOW + timedelta(hours=24), "S001": NOW + timedelta(minutes=1)}
    assert due([TG, GN], last, SCHEDULE, NOW) == [TG, GN]
