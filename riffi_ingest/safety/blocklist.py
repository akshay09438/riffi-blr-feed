"""The blocklist (blocklist.csv): copycat and unreliable sites. Any item whose URL is on one of them is
dropped (BRIEF.md "Filter"). Dangerous file (CLAUDE.md item 3).

blocklist.csv is written by people, so each row is read generously:
- every domain in the `name` and `url` columns counts ("a.online / b.store / c.store" blocks all three);
- a URL with a path blocks only that path (https://x.com/Cockroachisback blocks that account, not all of X);
- a row with no domain in it at all ("Khel Now fixture tables") cannot be applied, and is reported in
  `problems` so a person can add the domain - it is never guessed.

A domain blocks itself and every subdomain (blocking bengalurumetro.in also blocks www.bengalurumetro.in).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DOMAIN_RE = re.compile(r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(/[^\s,;|]*)?", re.I)


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


@dataclass
class Blocklist:
    domains: set[str] = field(default_factory=set)
    paths: set[tuple[str, str]] = field(default_factory=set)  # (domain, lower-case path prefix)
    problems: list[str] = field(default_factory=list)  # rows that could not be applied, for a person to fix

    def add(self, text: str) -> int:
        """Add every domain (or domain + path) found in `text`; return how many were found."""
        found = 0
        for m in DOMAIN_RE.finditer(text.replace("https://", " ").replace("http://", " ")):
            domain = m.group(1).lower()
            domain = domain[4:] if domain.startswith("www.") else domain
            path = (m.group(2) or "").rstrip("/").lower()
            if path:
                self.paths.add((domain, path))
            else:
                self.domains.add(domain)
            found += 1
        return found

    def match(self, url: str) -> str | None:
        """The blocklist entry that `url` falls under, or None."""
        host = _host(url)
        if not host:
            return None
        for domain in self.domains:
            if host == domain or host.endswith("." + domain):
                return domain
        path = (urlsplit(url).path or "").rstrip("/").lower()
        for domain, prefix in self.paths:
            if (host == domain or host.endswith("." + domain)) and (path == prefix or path.startswith(prefix + "/")):
                return domain + prefix
        return None


def load_blocklist(path: str | Path) -> Blocklist:
    blocklist = Blocklist()
    with open(path, encoding="utf-8", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            name, url = (row.get("name") or "").strip(), (row.get("url") or "").strip()
            # an X handle in the name ("@Cockroachisback (X)") is not a domain; the url column carries the link
            if not blocklist.add(name) + blocklist.add(url):
                blocklist.problems.append(f"blocklist.csv row {n}: no domain in {name!r} - add the site's domain")
    return blocklist
