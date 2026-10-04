"""The source list (feeds.csv, an export of Source List v4) read into one shape.

Storing sources in the database is step 6; until then the fetchers read the CSV directly.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Source:
    source_id: str
    name: str
    category: str
    route_type: str
    fetch_url: str
    link_or_handle: str = ""
    topics_hint: str = ""
    tier: str = ""
    priority_x_feed: bool = False
    backup_google_news_url: str = ""
    known_status: str = ""
    notes: str = ""


def load_sources(path: str | Path) -> list[Source]:
    with open(path, encoding="utf-8", newline="") as f:
        return [
            Source(
                source_id=row["source_id"].strip(),
                name=row["name"].strip(),
                category=row["category"].strip(),
                route_type=row["route_type"].strip(),
                fetch_url=row["fetch_url"].strip(),
                link_or_handle=row.get("link_or_handle", "").strip(),
                topics_hint=row.get("topics_hint", "").strip(),
                tier=row.get("tier", "").strip(),
                priority_x_feed=row.get("priority_x_feed", "").strip().lower() == "yes",
                backup_google_news_url=row.get("backup_google_news_url", "").strip(),
                known_status=row.get("known_status", "").strip(),
                notes=row.get("notes", "").strip(),
            )
            for row in csv.DictReader(f)
        ]
