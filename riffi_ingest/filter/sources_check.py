"""Picks come from news. A story whose every link is a user post (Reddit), a ticket or listing page, a live
tracker or a campaign site is kept in the digest but never picked: the team cannot check it like an article, and
a listing reads as an ad. The hosts and URL patterns live in config/filter.yaml (pick_sources)."""

from __future__ import annotations

import re
from functools import lru_cache
from urllib.parse import urlsplit

from .config import FilterConfig


@lru_cache(maxsize=8)
def _compiled(hosts: tuple[str, ...], patterns: tuple[str, ...]):
    url_re = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None
    return tuple(h.lower().lstrip(".") for h in hosts), url_re


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def not_news(url: str, cfg: FilterConfig) -> bool:
    block = cfg["pick_sources"]
    hosts, url_re = _compiled(tuple(block["exclude_hosts"]), tuple(block["exclude_url_patterns"]))
    host = _host(url)
    if any(host == h or host.endswith("." + h) for h in hosts):
        return True
    return bool(url_re and url_re.search(url))


def source_problem(links: list[str], cfg: FilterConfig) -> str:
    """'' when at least one link is a news page; otherwise a plain sentence. A story with no links is left alone."""
    if links and all(not_news(u, cfg) for u in links):
        return "its sources are user posts, listings, trackers or campaign pages, not news articles"
    return ""
