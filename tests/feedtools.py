"""Builders for fake feeds and a fake network, shared by the fetcher tests."""

import base64
import json
from datetime import datetime

import httpx

from riffi_ingest.fetchers.http import PoliteClient

RSS = """<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<title>{feed_title}</title>
{items}
</channel></rss>"""

ITEM = "<item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate><description>{summary}</description>{extra}</item>"


def rfc822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")


def rss(items, feed_title="Test feed") -> bytes:
    body = "\n".join(ITEM.format(**{"summary": "", "extra": "", **i}) for i in items)
    return RSS.format(feed_title=feed_title, items=body).encode()


def fake_client(handler, **kw) -> PoliteClient:
    """A PoliteClient on a fake network, with no politeness delays and no retries unless asked."""
    kw.setdefault("retries", 0)
    kw.setdefault("interval", (0.0, 0.0))
    return PoliteClient(transport=httpx.MockTransport(handler), **kw)


def old_style(url: str) -> str:
    """A Google News article id that carries its publisher URL (decodes offline)."""
    return base64.urlsafe_b64encode(b'\x08\x13"\x1a' + url.encode() + b"\xd2\x01\x00").decode().rstrip("=")


def new_style(tail: bytes) -> str:
    """A Google News article id that needs the online decode."""
    return base64.urlsafe_b64encode(b'\x08\x13"AU_yqL' + tail).decode().rstrip("=")


NEW_STYLE = new_style(b"xyz")


def batch_answer(url: str) -> str:
    """Google's batchexecute answer naming `url` as the publisher URL."""
    inner = json.dumps(["garturlres", url, 1])
    return ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"]]) + "\n\n"
