"""The blocklist (blocklist.csv): copycat and unreliable sites. Any item whose URL is on one of them is
dropped (BRIEF.md "Filter"). Dangerous file (CLAUDE.md item 3).

blocklist.csv is written by people, so each row is read generously:
- every domain in the `name` and `url` columns counts ("a.online / b.store / c.store" blocks all three);
  words that only look like domains (file names such as "notice.pdf", "Node.js") do not;
- a URL with a path blocks only that path (https://x.com/Cockroachisback blocks that account, not all of X);
- a row with no domain in it at all ("Khel Now fixture tables") cannot be applied, and is reported in
  `problems` so a person can add the domain - it is never guessed.

Matching is strict about what a link really points at, because copycats are what this list is for:
- a domain blocks itself and every subdomain (bengalurumetro.in also blocks www.bengalurumetro.in);
- the host is read the way a browser reads it: case, trailing dot, port, user-info ("good.com@bad.in"),
  backslashes ("bad.in\\@good.com" goes to bad.in) and full-width dots ("bad。in") are all normalised;
- AMP copies hosted by Google or the AMP cache (google.com/amp/s/bad.in/..., bad-in.cdn.ampproject.org)
  are checked as the page they copy;
- x.com and twitter.com are the same site, and paths are compared decoded, without doubled slashes.
- Never raises: a link that cannot be read is simply not matched.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit

DOMAIN_RE = re.compile(r"(?<![\w@.-])((?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,})(/[^\s,;|]*)?", re.I)
# endings that make a word look like a domain but are file types or tool names, never sites here
NOT_TLDS = {
    "pdf",
    "js",
    "csv",
    "xls",
    "xlsx",
    "doc",
    "docx",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "html",
    "htm",
    "txt",
    "md",
    "py",
}
HOST_ALIASES = {"twitter.com": "x.com"}
DOTS = str.maketrans({"。": ".", "．": ".", "｡": "."})
AMP_CACHE_SUFFIX = ".cdn.ampproject.org"


def _bare(host: str) -> str:
    host = host.lower().rstrip(".")
    host = host[4:] if host.startswith("www.") else host
    for alias, real in HOST_ALIASES.items():
        if host == alias or host.endswith("." + alias):
            return host[: -len(alias)] + real
    return host


def _split(url: str) -> tuple[str, str]:
    """(host, path) the way a browser would read `url`; ("", "") when it cannot be read."""
    text = unicodedata.normalize("NFKC", url.strip()).translate(DOTS).replace("\\", "/")
    if "://" not in text and not text.startswith("//"):
        text = "//" + text
    try:
        parts = urlsplit(text)
        host = parts.hostname or ""
    except ValueError:
        return "", ""
    path = re.sub(r"/{2,}", "/", unquote(parts.path or "")).rstrip("/").lower()
    return _bare(host), path


def _amp_original(host: str, path: str) -> str | None:
    """The page an AMP copy stands for, or None if this is not an AMP copy."""
    if host.endswith(AMP_CACHE_SUFFIX) or (host in ("google.com", "google.co.in") and path.startswith("/amp/")):
        # /c/s/<host>/<path>, /v/s/<host>/<path>, /amp/s/<host>/<path>, /amp/<host>/<path>
        m = re.match(r"^/(?:[cvi]/)?(?:amp/)?(?:s/)?([^/]+)(/.*)?$", path)
        if m:
            return m.group(1) + (m.group(2) or "")
    if host.endswith(AMP_CACHE_SUFFIX):
        # the cache's own subdomain spells the original host: www-bad-in -> www.bad.in, a--b -> a-b
        label = host[: -len(AMP_CACHE_SUFFIX)]
        return label.replace("--", "\0").replace("-", ".").replace("\0", "-") + path
    return None


@dataclass
class Blocklist:
    domains: set[str] = field(default_factory=set)
    paths: set[tuple[str, str]] = field(default_factory=set)  # (domain, lower-case path prefix)
    problems: list[str] = field(default_factory=list)  # rows that could not be applied, for a person to fix

    def add(self, text: str) -> int:
        """Add every domain (or domain + path) found in `text`; return how many were found."""
        found = 0
        for m in DOMAIN_RE.finditer(text.replace("https://", " ").replace("http://", " ")):
            if m.group(1).rsplit(".", 1)[-1].lower() in NOT_TLDS:
                continue
            domain = _bare(m.group(1))
            path = re.sub(r"/{2,}", "/", unquote(m.group(2) or "")).rstrip("/").lower()
            if path:
                self.paths.add((domain, path))
            else:
                self.domains.add(domain)
            found += 1
        return found

    def _match_host_path(self, host: str, path: str) -> str | None:
        if not host:
            return None
        for domain in self.domains:
            if host == domain or host.endswith("." + domain):
                return domain
        for domain, prefix in self.paths:
            if (host == domain or host.endswith("." + domain)) and (path == prefix or path.startswith(prefix + "/")):
                return domain + prefix
        return None

    def match(self, url: str) -> str | None:
        """The blocklist entry that `url` falls under (directly or as an AMP copy), or None."""
        if not url:
            return None
        host, path = _split(url)
        hit = self._match_host_path(host, path)
        if hit is None and (original := _amp_original(host, path)):
            hit = self._match_host_path(*_split(original))
        return hit


def load_blocklist(path: str | Path) -> Blocklist:
    blocklist = Blocklist()
    with open(path, encoding="utf-8", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            name, url = (row.get("name") or "").strip(), (row.get("url") or "").strip()
            # an X handle in the name ("@Cockroachisback (X)") is not a domain; the url column carries the link
            if not blocklist.add(name) + blocklist.add(url):
                blocklist.problems.append(f"blocklist.csv row {n}: no domain in {name!r} - add the site's domain")
    return blocklist
