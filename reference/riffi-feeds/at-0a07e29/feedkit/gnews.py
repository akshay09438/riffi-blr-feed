"""Google News RSS helpers.

Google News RSS links point at news.google.com/rss/articles/<id>, not the publisher.
Older ids decode offline (base64 holds the URL). Newer ids ("AU_yqL...") need the
same two calls a browser makes: read the signature and timestamp from the article
page, then ask Google's batchexecute endpoint for the publisher URL.
"""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import quote, quote_plus, urlsplit

from .net import PoliteClient

ID_RE = re.compile(r"/(?:rss/)?articles/([A-Za-z0-9_\-]+)")
SG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')
URL_IN_BYTES = re.compile(rb"https?://[\x21-\x7e]+")
BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"

LANG_PARAMS = {
    "EN": "hl=en-IN&gl=IN&ceid=IN:en",
    "KN": "hl=kn&gl=IN&ceid=IN:kn",
    "HI": "hl=hi&gl=IN&ceid=IN:hi",
}


def is_gnews(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or "").endswith("news.google.com") and parts.path.startswith("/rss")


def is_gnews_article(url: str) -> bool:
    return (urlsplit(url).hostname or "").endswith("news.google.com") and bool(ID_RE.search(url))


def search_url(query: str, language: str = "EN") -> str:
    lang = LANG_PARAMS.get((language or "EN").split("/")[0].upper(), LANG_PARAMS["EN"])
    q = quote_plus(query, safe=":()")
    return f"https://news.google.com/rss/search?q={q}&{lang}"


def article_id(url: str) -> str | None:
    m = ID_RE.search(urlsplit(url).path)
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
    if not m:
        return None
    return m.group(0).decode("ascii", errors="ignore")


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
                inner = json.loads(item[2])
                if len(inner) > 1 and isinstance(inner[1], str) and inner[1].startswith("http"):
                    return inner[1]
    return None


async def resolve(client: PoliteClient, gn_url: str) -> tuple[str | None, str]:
    """Return (publisher_url, method_or_error)."""
    art_id = article_id(gn_url)
    if not art_id:
        return None, "not a google news article url"
    offline = decode_offline(art_id)
    if offline:
        return offline, "offline-base64"
    page = await client.fetch(f"https://news.google.com/rss/articles/{art_id}", ua="browser", retries=1)
    if not page.ok:
        return None, f"article page {page.status or page.error_kind}"
    sg, ts = SG_RE.search(page.text), TS_RE.search(page.text)
    if not (sg and ts):
        return None, "no signature on article page"
    req = [
        "Fbv4je",
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
        f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{art_id}",{ts.group(1)},"{sg.group(1)}"]',
    ]
    payload = "f.req=" + quote(json.dumps([[req]]))
    res = await client.fetch(
        BATCH_URL,
        ua="browser",
        method="POST",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
        retries=1,
    )
    if not res.ok:
        return None, f"batchexecute {res.status or res.error_kind}"
    url = _extract_batch_url(res.text)
    return (url, "batchexecute") if url else (None, "batchexecute: no url in response")
