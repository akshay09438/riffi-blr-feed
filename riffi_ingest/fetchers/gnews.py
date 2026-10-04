"""Google News: recognise its feeds and resolve its article links to the real publisher URL.
Dangerous file (CLAUDE.md item 8): D-004 records that the founder chose Google News knowing
news.google.com/robots.txt disallows /rss for bots; how often we ask Google is decided here.

Google News RSS links point at news.google.com/rss/articles/<id>, not the publisher. Older ids
decode offline (the base64 holds the URL). Newer ids ("AU_yqL...") need the same two calls a browser
makes: read the signature and timestamp from the article page, then ask Google's batchexecute
endpoint for the publisher URL. That is two requests to Google per article, so every answer is
cached and online resolutions are capped per run (a lesson from the panel project).

Adapted from the panel project's feedkit/gnews.py at commit 0a07e29 (copied, not imported - D-006).
"""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import quote, urlsplit

from .http import PAGE_ACCEPT, PoliteClient

ID_RE = re.compile(r"/(?:rss/)?articles/([A-Za-z0-9_\-]+)")
SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')
URL_IN_BYTES = re.compile(rb"https?://[\x21-\x7e]+")
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
ONLINE_CAP_PER_RUN = 100  # at two requests each and 2 s per request to Google, about 7 minutes


def is_gnews_feed(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or "").endswith("news.google.com") and parts.path.startswith("/rss")


def is_google(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "google.com" or host.endswith(".google.com")


def article_id(url: str) -> str | None:
    parts = urlsplit(url)
    if not (parts.hostname or "").endswith("news.google.com"):
        return None
    m = ID_RE.search(parts.path)
    return m.group(1) if m else None


def decode_offline(art_id: str) -> str | None:
    """Old-style ids carry the publisher URL inside the base64 payload."""
    try:
        raw = base64.urlsafe_b64decode(art_id + "=" * (-len(art_id) % 4))
    except (ValueError, TypeError):
        return None
    if b"AU_yqL" in raw:
        return None  # new-style id, needs the online decode
    m = URL_IN_BYTES.search(raw)
    return m.group(0).decode("ascii", errors="ignore") if m else None


def _extract_batch_url(text: str) -> str | None:
    body = text.split("\n", 1)[1] if text.startswith(")]}'") else text
    for block in body.split("\n\n"):
        block = block.strip()
        if not block.startswith("["):
            continue
        try:
            outer = json.loads(block)
        except json.JSONDecodeError:
            continue
        for item in outer:
            if isinstance(item, list) and len(item) > 2 and isinstance(item[2], str) and "garturlres" in item[2]:
                try:
                    inner = json.loads(item[2])
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(inner, list)
                    and len(inner) > 1
                    and isinstance(inner[1], str)
                    and inner[1].startswith("http")
                ):
                    return inner[1]
    return None


async def _resolve_online(client: PoliteClient, art_id: str) -> tuple[str | None, str]:
    page = await client.fetch(f"https://news.google.com/rss/articles/{art_id}", accept=PAGE_ACCEPT, retries=1)
    if not page.ok:
        return None, f"article page {page.status or page.error_kind}"
    if page.redirects and not is_google(page.final_url):
        return page.final_url, "redirect"  # Google sent us straight to the publisher
    sg, ts = SG_RE.search(page.text), TS_RE.search(page.text)
    if not (sg and ts):
        return None, "no signature on article page"
    req = [
        "Fbv4je",
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
        + f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{art_id}",{ts.group(1)},"{sg.group(1)}"]',
    ]
    res = await client.fetch(
        BATCH_URL,
        accept="*/*",
        method="POST",
        data="f.req=" + quote(json.dumps([[req]])),
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        retries=1,
    )
    if not res.ok:
        return None, f"batchexecute {res.status or res.error_kind}"
    url = _extract_batch_url(res.text)
    return (url, "batchexecute") if url else (None, "batchexecute: no url in response")


class Resolver:
    """Resolves Google News article links for one run. Successful answers are cached by article id
    (pass `cache` to carry them across runs); online resolutions stop at `online_cap` for the run, and
    the links left over stay as Google News links until a later run resolves them."""

    def __init__(self, client: PoliteClient, *, cache: dict[str, str] | None = None, online_cap=ONLINE_CAP_PER_RUN):
        self.client = client
        self.cache = cache if cache is not None else {}
        self.online_cap = online_cap
        self.online_used = 0

    async def resolve(self, gn_url: str) -> tuple[str | None, str]:
        """Return (publisher_url, how) - `how` names the method, or why there is no URL."""
        art_id = article_id(gn_url)
        if not art_id:
            return None, "not a google news article url"
        if art_id in self.cache:
            return self.cache[art_id], "cache"
        url = decode_offline(art_id)
        how = "offline-base64"
        if not url:
            if self.online_used >= self.online_cap:
                return None, "online cap reached for this run"
            self.online_used += 1
            try:
                url, how = await _resolve_online(self.client, art_id)
            except Exception as exc:  # an odd answer from Google must not stop the run
                url, how = None, f"{type(exc).__name__}: {exc}"[:200]
        if url:
            self.cache[art_id] = url
        return url, how
