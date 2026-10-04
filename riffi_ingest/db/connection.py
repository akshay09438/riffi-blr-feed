"""Opening the database: one place for the settings every connection needs."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .schema import SCHEMA, SCHEMA_VERSION

# Anchored on the project folder, not the current folder: Windows Task Scheduler starts programs in
# C:\Windows\System32, and a relative path would quietly create a second, empty database there.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "engine.db"


def default_db_path() -> Path:
    """RIFFI_DB_PATH if set, else <project>/data/engine.db."""
    return Path(os.environ["RIFFI_DB_PATH"]) if os.environ.get("RIFFI_DB_PATH") else DEFAULT_DB_PATH


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


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the database, with WAL, foreign keys and a 30 s busy timeout."""
    path = Path(path) if path is not None else default_db_path()
    if any(part.lower().startswith("onedrive") for part in path.resolve().parts):
        # D-001: sync tools copy the -wal / -shm files separately from the database and can corrupt it
        raise RuntimeError(f"{path} is inside OneDrive; keep the database in a folder that is not synced (D-001)")
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")  # BUSY_TIMEOUT_S, in milliseconds
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    # one row only (id = 1), so two processes creating the file at once cannot leave two versions
    conn.execute("INSERT OR IGNORE INTO schema_version (id, version) VALUES (1, ?)", (SCHEMA_VERSION,))
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    if row["version"] > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(f"{path} was written by a newer engine (schema {row['version']} > {SCHEMA_VERSION})")
    conn.commit()
    return conn
