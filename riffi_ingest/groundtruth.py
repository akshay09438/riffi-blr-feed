"""The editor's ground-truth log (BRIEF.md "Two-week test", step 10): the real developments on High-priority
topics, seen anywhere, one row each. The recall test scores the sources against it, so a typo in a row is a
wrong verdict on a source: `check-log` catches them the same day.

The log is a plain CSV the editor keeps in Excel or Google Sheets, with these columns (extra columns are kept
out of the way, never an error):

    date           the day it was seen, YYYY-MM-DD (DD-MM-YYYY and DD/MM/YYYY also work: Excel's Indian dates)
    topic_id       from topics.csv, e.g. P01
    what_happened  one line
    where_seen     X, WhatsApp, TV, a friend, a site...
    time_seen      IST, 24-hour HH:MM (9:30 PM also works)

Matching the log to stories, recall and the day-14 report come with step 10; they read the log through
`read_log`, so the format is settled here once.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .fetchers.parse import IST

COLUMNS = ("date", "topic_id", "what_happened", "where_seen", "time_seen")
DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y")
TIME_FORMATS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p", "%I:%M%p")


@dataclass(frozen=True)
class Topic:
    topic_id: str
    topic: str
    date_or_window: str
    priority: str


@dataclass(frozen=True)
class Entry:
    row: int  # the spreadsheet row number (the header is row 1), so a problem points at the right line
    seen_at: datetime  # IST, timezone-aware
    topic_id: str
    what_happened: str
    where_seen: str


@dataclass
class LogCheck:
    entries: list[Entry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)  # the row cannot be scored until fixed
    warnings: list[str] = field(default_factory=list)  # scored, but worth a look


def load_topics(path: str | Path) -> dict[str, Topic]:
    with open(path, encoding="utf-8", newline="") as f:
        return {
            t.topic_id: t
            for t in (
                Topic(
                    row["topic_id"].strip().upper(),
                    row["topic"].strip(),
                    row.get("date_or_window", "").strip(),
                    row.get("priority", "").strip(),
                )
                for row in csv.DictReader(f)
            )
        }


def write_template(log_path: str | Path, topics_path: str | Path, topics: dict[str, Topic]) -> bool:
    """Write the empty log and the list of High-priority topics to log from. Never overwrites an existing log
    (it holds the editor's work; returns False). The topic list is regenerated every time."""
    log_path, topics_path = Path(log_path), Path(topics_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    topics_path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: Excel only reads Kannada and the rupee sign correctly from a UTF-8 file that starts with a BOM
    with open(topics_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["topic_id", "topic", "date_or_window"])
        for t in sorted(topics.values(), key=lambda t: t.topic_id):
            if t.priority == "High":
                w.writerow([t.topic_id, t.topic, t.date_or_window])
    if log_path.exists():
        return False
    with open(log_path, "x", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(COLUMNS)
    return True


def _parse(value: str, formats: tuple[str, ...]):
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def read_log(path: str | Path, topics: dict[str, Topic], now: datetime) -> LogCheck:
    """Every row of the log, checked. Rows with an error are left out of `entries`; blank rows are skipped."""
    check = LogCheck()
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
    except UnicodeDecodeError:
        check.errors.append(
            "the file is not saved as UTF-8. In Excel use File > Save As > 'CSV UTF-8 (Comma delimited)', "
            "otherwise Kannada text is lost."
        )
        return check
    if not rows:
        check.errors.append(f"the file is empty; it needs the header row: {','.join(COLUMNS)}")
        return check
    header = [h.strip().lower().replace(" ", "_") for h in rows[0]]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        check.errors.append(f"row 1: missing column(s) {', '.join(missing)}; the header must be {','.join(COLUMNS)}")
        return check
    at = {c: header.index(c) for c in COLUMNS}
    seen: dict[tuple[str, str, str], int] = {}
    for n, raw in enumerate(rows[1:], start=2):
        cell = {c: (raw[i].strip() if i < len(raw) else "") for c, i in at.items()}
        if not any(cell.values()):
            continue
        problems = [f"row {n}: '{c}' is empty" for c in COLUMNS if not cell[c]]
        day = _parse(cell["date"], DATE_FORMATS) if cell["date"] else None
        if cell["date"] and day is None:
            problems.append(f"row {n}: date '{cell['date']}' is not a date; write it as YYYY-MM-DD, e.g. 2026-10-12")
        clock = _parse(cell["time_seen"].upper(), TIME_FORMATS) if cell["time_seen"] else None
        if cell["time_seen"] and clock is None:
            problems.append(f"row {n}: time_seen '{cell['time_seen']}' is not a time; write it as HH:MM, e.g. 14:30")
        topic_id = cell["topic_id"].upper()
        if topic_id and topic_id not in topics:
            problems.append(f"row {n}: topic_id '{cell['topic_id']}' is not in topics.csv")
        seen_at = None
        if day and clock:
            seen_at = datetime.combine(day.date(), clock.time(), tzinfo=IST)
            if seen_at > now.astimezone(IST):
                problems.append(f"row {n}: {seen_at:%Y-%m-%d %H:%M} IST is in the future")
        if problems:
            check.errors.extend(problems)
            continue
        if topics[topic_id].priority != "High":
            check.warnings.append(
                f"row {n}: {topic_id} is a {topics[topic_id].priority}-priority topic; the test scores High ones"
            )
        key = (seen_at.date().isoformat(), topic_id, " ".join(cell["what_happened"].lower().split()))
        if key in seen:
            check.warnings.append(f"row {n}: the same development as row {seen[key]}; it will be counted once")
            continue
        seen[key] = n
        check.entries.append(Entry(n, seen_at, topic_id, cell["what_happened"], cell["where_seen"]))
    return check
