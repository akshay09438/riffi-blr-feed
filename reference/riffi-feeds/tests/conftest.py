import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedkit.net as net  # noqa: E402
from fetcher import reddit_signal  # noqa: E402


@pytest.fixture(autouse=True)
def fast_gates(monkeypatch):
    """No politeness delays against the fake network used in tests."""
    monkeypatch.setattr(net, "DEFAULT_INTERVAL", (0.0, 0.0))
    monkeypatch.setattr(net, "SLOW_LANES", {})


@pytest.fixture(autouse=True)
def reddit_machinery_testable(monkeypatch):
    """Reddit is ceased (founder, 1 Oct 2026: reddit_signal.CEASED is True). Its code is kept, unused,
    until the founder decides to delete it, so the older tests of that code run with the switch off;
    tests/test_reddit_ceased.py switches it back on and proves nothing Reddit gets through."""
    monkeypatch.setattr(reddit_signal, "CEASED", False)


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title>
{items}
</channel></rss>"""

ITEM = "<item><title>{title}</title><link>{link}</link><pubDate>{date}</pubDate><description>{summary}</description></item>"


def rss(items):
    return RSS.format(items="\n".join(ITEM.format(**i) for i in items)).encode()
