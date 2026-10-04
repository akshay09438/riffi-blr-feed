"""What every fetcher returns, whatever the route."""

from __future__ import annotations

from dataclasses import dataclass, field

from .parse import Entry


@dataclass
class Validators:
    """What the last successful fetch of a URL answered, sent back as a conditional GET."""

    etag: str = ""
    last_modified: str = ""


@dataclass(frozen=True)
class PageSnapshot:
    """A web page monitor's memory of a page: the text that counts and its hash."""

    text_hash: str
    text: str
    title: str = ""


@dataclass
class FetchOutcome:
    source_id: str
    route_type: str
    status: str  # ok | not_modified | skipped | error
    url: str = ""
    on_backup: bool = False  # an X/Instagram row read through its backup Google News query
    reason: str = ""  # why it was skipped, or what went wrong
    http_status: int | None = None
    error_kind: str | None = None
    feed_title: str = ""
    entries: list[Entry] = field(default_factory=list)
    validators: Validators = field(default_factory=Validators)
    snapshot: PageSnapshot | None = None  # page monitors only: the page as last seen, to store for next time
    elapsed: float = 0.0
