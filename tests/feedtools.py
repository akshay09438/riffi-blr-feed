"""Builders for fake feeds and a fake network, shared by the fetcher tests."""

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
