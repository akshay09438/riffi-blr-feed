"""Storage (BRIEF.md step 6). Dangerous (CLAUDE.md item 2): this holds the two-week test's fetch history
and the editor's ground truth, which cannot be recreated.

One SQLite file (data/engine.db by default). Every date is stored as an ISO-8601 string in UTC, so text
order is time order; it is read back as a timezone-aware datetime.
"""

from .connection import DEFAULT_DB_PATH, connect

__all__ = ["DEFAULT_DB_PATH", "connect"]
