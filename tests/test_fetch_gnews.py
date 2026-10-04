import asyncio
import base64
import json

import httpx

from riffi_ingest.fetchers import gnews
from tests.feedtools import fake_client


def run(coro):
    return asyncio.run(coro)


def old_style(url: str) -> str:
    return base64.urlsafe_b64encode(b'\x08\x13"\x1a' + url.encode() + b"\xd2\x01\x00").decode().rstrip("=")


def new_style(tail: bytes) -> str:
    return base64.urlsafe_b64encode(b'\x08\x13"AU_yqL' + tail).decode().rstrip("=")


NEW_STYLE = new_style(b"xyz")


def batch_answer(url: str) -> str:
    inner = json.dumps(["garturlres", url, 1])
    return ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"]]) + "\n\n"


def google(handler_calls: list):
    def handler(request):
        handler_calls.append(request.url.path)
        if request.url.path.startswith("/rss/articles/"):
            return httpx.Response(200, text='<div data-n-a-sg="SIG" data-n-a-ts="1759550000"></div>')
        return httpx.Response(200, text=batch_answer("https://www.thehindu.com/news/x.ece"))

    return handler


def test_feed_and_article_detection():
    assert gnews.is_gnews_feed("https://news.google.com/rss/search?q=Shivakumar&hl=en-IN&gl=IN&ceid=IN:en")
    assert not gnews.is_gnews_feed("https://www.thehindu.com/feeder/default.rss")
    assert gnews.article_id("https://news.google.com/rss/articles/CBMiabc?oc=5") == "CBMiabc"
    assert gnews.article_id("https://example.com/rss/articles/CBMiabc") is None


def test_offline_decode_of_old_style_ids():
    assert gnews.decode_offline(old_style("https://example.in/story-1")) == "https://example.in/story-1"
    assert gnews.decode_offline(NEW_STYLE) is None


def test_batchexecute_answer_parsing():
    assert gnews._extract_batch_url(batch_answer("https://www.thehindu.com/news/x.ece")) == (
        "https://www.thehindu.com/news/x.ece"
    )
    assert gnews._extract_batch_url(')]}\'\n\n[["wrb.fr", "Fbv4je", "garturlres broken"]]') is None


def test_resolver_uses_offline_decode_without_asking_google():
    calls = []

    async def go():
        async with fake_client(google(calls)) as client:
            r = gnews.Resolver(client)
            return await r.resolve(f"https://news.google.com/rss/articles/{old_style('https://a.in/1')}?oc=5")

    assert run(go()) == ("https://a.in/1", "offline-base64")
    assert calls == []


def test_resolver_goes_online_once_then_answers_from_cache():
    calls = []
    link = f"https://news.google.com/rss/articles/{NEW_STYLE}?oc=5"

    async def go():
        async with fake_client(google(calls)) as client:
            r = gnews.Resolver(client)
            return await r.resolve(link), await r.resolve(link), r.cache

    first, second, cache = run(go())
    assert first == ("https://www.thehindu.com/news/x.ece", "batchexecute")
    assert second == ("https://www.thehindu.com/news/x.ece", "cache")
    assert len(calls) == 2  # article page + batchexecute, once
    assert cache == {NEW_STYLE: "https://www.thehindu.com/news/x.ece"}


def test_resolver_stops_asking_google_at_the_cap():
    calls = []

    async def go():
        async with fake_client(google(calls)) as client:
            r = gnews.Resolver(client, online_cap=1)
            a = await r.resolve(f"https://news.google.com/rss/articles/{NEW_STYLE}")
            b = await r.resolve(f"https://news.google.com/rss/articles/{new_style(b'other')}")
            return a, b

    a, b = run(go())
    assert a[0] and b == (None, "online cap reached for this run")
    assert len(calls) == 2


def test_resolver_reports_a_failed_lookup():
    def handler(request):
        return httpx.Response(200, text="<html>no signature here</html>")

    async def go():
        async with fake_client(handler) as client:
            return await gnews.Resolver(client).resolve(f"https://news.google.com/rss/articles/{NEW_STYLE}")

    assert run(go()) == (None, "no signature on article page")


def test_an_odd_batchexecute_answer_is_not_a_crash():
    odd = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", json.dumps({"garturlres": 1})]]) + "\n\n"
    assert gnews._extract_batch_url(odd) is None


def test_a_google_redirect_straight_to_the_publisher_counts_as_resolved():
    def handler(request):
        if request.url.host == "news.google.com":
            return httpx.Response(302, headers={"Location": "https://www.deccanherald.com/city/x"})
        return httpx.Response(200, text="<html>article</html>")

    async def go():
        async with fake_client(handler) as client:
            return await gnews.Resolver(client).resolve(f"https://news.google.com/rss/articles/{NEW_STYLE}")

    assert run(go()) == ("https://www.deccanherald.com/city/x", "redirect")
