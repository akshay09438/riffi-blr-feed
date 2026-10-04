"""Opening the database: one place for the settings every connection needs."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .schema import SCHEMA, SCHEMA_VERSION

DEFAULT_DB_PATH = Path("data") / "engine.db"
BUSY_TIMEOUT_S = 30  # the panel project needed 30 s+ when the scheduler and a CLI command overlap


def utc_iso(when: datetime | None) -> str | None:
    """A datetime as stored: ISO-8601 in UTC (text order = time order). Naive datetimes are taken as UTC."""
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the database, with WAL, foreign keys and a 30 s busy timeout."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")  # BUSY_TIMEOUT_S, in milliseconds
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(f"{path} was written by a newer engine (schema {row['version']} > {SCHEMA_VERSION})")
    conn.commit()
    return conn
