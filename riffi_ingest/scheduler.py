"""Step 7 (BRIEF.md "STEP 7: SCHEDULE", D-008): which sources are due for a fetch.

Windows Task Scheduler starts `python -m riffi_ingest fetch --due` every 30 minutes
(scripts/schedule-windows.ps1). Each time, only the sources whose interval is up are fetched, counted from
their last attempt (the latest fetch_runs.started_at, whatever its status). After the laptop has slept or been
off, the next tick finds every overdue source due once: one catch-up run, never a pile-up.

How often each source is fetched lives in config/schedule.yaml (team-editable): a speed per route type, plus
per-source overrides. A route type with no speed is never fetched; `problems` names it so a person sees it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from .sources import Source

_SPEED = re.compile(r"(\d+)([mh])")


class ScheduleError(ValueError):
    pass


def parse_interval(text: object) -> timedelta | None:
    """'30m' -> 30 minutes, '2h' -> 2 hours, 'never' -> None."""
    value = str(text).strip().lower()
    if value == "never":
        return None
    m = _SPEED.fullmatch(value)
    if not m or int(m.group(1)) == 0:
        raise ScheduleError(f"not a speed: {text!r} (use e.g. 30m, 2h or never)")
    n = int(m.group(1))
    return timedelta(minutes=n) if m.group(2) == "m" else timedelta(hours=n)


@dataclass
class Schedule:
    by_route: dict[str, timedelta | None]
    overrides: dict[str, timedelta | None] = field(default_factory=dict)  # source_id (upper case) -> speed
    early: timedelta = timedelta(minutes=5)  # due this much before the interval is up, so timer jitter costs nothing

    @classmethod
    def load(cls, path: str | Path) -> Schedule:
        try:
            raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
            return cls(
                by_route={str(k).strip(): parse_interval(v) for k, v in (raw.get("by_route") or {}).items()},
                overrides={str(k).strip().upper(): parse_interval(v) for k, v in (raw.get("sources") or {}).items()},
                early=timedelta(minutes=int(raw.get("early_minutes", 5))),
            )
        except (ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
            raise ScheduleError(f"{path}: {exc}") from exc

    def interval(self, source: Source) -> timedelta | None:
        """How often `source` is fetched; None means never."""
        source_id = source.source_id.upper()
        if source_id in self.overrides:
            return self.overrides[source_id]
        return self.by_route.get(source.route_type)

    def problems(self, sources: list[Source]) -> list[str]:
        """Route types with no speed (their sources would never be fetched) and overrides naming no source."""
        routes = sorted({s.route_type for s in sources} - set(self.by_route))
        unknown = sorted(set(self.overrides) - {s.source_id.upper() for s in sources})
        return [f"no speed for route type {r!r}" for r in routes] + [
            f"a speed for unknown or inactive source {u}" for u in unknown
        ]


def due(sources: list[Source], last_attempted: dict[str, datetime], schedule: Schedule, now: datetime) -> list[Source]:
    """The sources to fetch now: never tried, or last tried at least (interval - early) ago."""
    out = []
    for source in sources:
        every = schedule.interval(source)
        if every is None:
            continue
        last = last_attempted.get(source.source_id)
        if last is None or now - last >= every - schedule.early:
            out.append(source)
    return out
