import asyncio
import time

import httpx

from riffi_ingest.fetchers.http import BROWSER_UA, PoliteClient, domain_key
from tests.feedtools import fake_client


def run(coro):
    return asyncio.run(coro)


def test_sends_a_browser_identity():
    # D-004: the founder chose a browser-like User-Agent over the panel project's bot identity
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, text="ok")

    async def go():
        async with fake_client(handler) as client:
            return await client.fetch("https://a.in/feed")

    assert run(go()).ok
    assert seen["user-agent"] == BROWSER_UA and "Mozilla/5.0" in BROWSER_UA


def test_conditional_get_sends_validators_and_reports_not_modified():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(304)

    async def go():
        async with fake_client(handler) as client:
            return await client.fetch("https://a.in/feed", etag='"v1"', last_modified="Sat, 03 Oct 2026 10:00:00 GMT")

    res = run(go())
    assert res.not_modified and not res.ok
    assert seen["if-none-match"] == '"v1"'
    assert seen["if-modified-since"] == "Sat, 03 Oct 2026 10:00:00 GMT"


def test_validators_are_read_from_the_answer():
    def handler(request):
        return httpx.Response(
            200, text="x", headers={"ETag": '"abc"', "Last-Modified": "Sun, 04 Oct 2026 01:00:00 GMT"}
        )

    async def go():
        async with fake_client(handler) as client:
            return await client.fetch("https://a.in/feed")

    res = run(go())
    assert res.etag == '"abc"' and res.last_modified == "Sun, 04 Oct 2026 01:00:00 GMT"


def test_retries_on_5xx_then_succeeds(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503 if len(calls) == 1 else 200, text="ok")

    async def no_sleep(_):
        return None

    async def go():
        async with fake_client(handler, retries=2) as client:
            monkeypatch.setattr(asyncio, "sleep", no_sleep)
            return await client.fetch("https://a.in/feed")

    res = run(go())
    assert res.ok and res.attempts == 2 and len(calls) == 2


def test_a_404_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    async def go():
        async with fake_client(handler, retries=2) as client:
            return await client.fetch("https://a.in/gone")

    res = run(go())
    assert not res.ok and res.status == 404 and len(calls) == 1


def test_network_failure_is_a_result_not_a_crash():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    async def go():
        async with fake_client(handler) as client:
            return await client.fetch("https://down.example/feed")

    res = run(go())
    assert res.status is None and res.error_kind == "connect" and "refused" in res.error


def test_gives_up_on_a_slow_drip_server():
    """NSE answers one byte at a time; the overall deadline must still fire."""

    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(50):
                await asyncio.sleep(0.1)
                yield b"<"

    async def go():
        client = PoliteClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Drip())), retries=0)
        client.deadline = 0.3
        async with client:
            return await client.fetch("https://slow.example/feed")

    res = run(go())
    assert res.error_kind == "timeout" and res.status is None


def test_one_domain_waits_between_requests_but_other_domains_do_not():
    starts = []

    def handler(request):
        starts.append((request.url.host, time.monotonic()))
        return httpx.Response(200, text="ok")

    async def go():
        async with fake_client(handler, interval=(0.3, 0.3)) as client:
            await asyncio.gather(
                client.fetch("https://a.in/1"), client.fetch("https://www.a.in/2"), client.fetch("https://b.in/1")
            )

    run(go())
    a = sorted(t for host, t in starts if host.endswith("a.in"))
    b = [t for host, t in starts if host == "b.in"]
    assert a[1] - a[0] >= 0.29  # www.a.in and a.in are one domain
    assert b[0] - a[0] < 0.2  # b.in did not queue behind a.in


def test_domain_key_ignores_www():
    assert domain_key("https://www.thehindu.com/x") == "thehindu.com"
    assert domain_key("https://news.google.com/rss") == "news.google.com"


# ---- found by the adversarial review, 4 Oct 2026


def test_a_nonsense_retry_after_does_not_hang_the_run(monkeypatch):
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "nan"})

    async def go():
        async with fake_client(handler, retries=1) as client:
            monkeypatch.setattr(asyncio, "sleep", fake_sleep)
            return await client.fetch("https://a.in/feed")

    res = run(go())
    assert res.status == 429 and res.attempts == 2
    assert waits == [2.0]  # the normal backoff, not sleep(nan)


def test_cancelling_a_queued_request_does_not_lock_the_domain():
    def handler(request):
        return httpx.Response(200, text="ok")

    async def go():
        async with fake_client(handler, interval=(0.5, 0.5)) as client:
            await client.fetch("https://b.in/1")  # next b.in request must now wait 0.5 s
            waiting = asyncio.create_task(client.fetch("https://b.in/2"))
            await asyncio.sleep(0.05)
            waiting.cancel()
            return await asyncio.wait_for(client.fetch("https://b.in/3"), timeout=2)

    assert run(go()).ok


def test_each_redirect_hop_waits_its_turn_on_its_own_domain():
    starts = []

    def handler(request):
        starts.append((request.url.host, request.url.path, time.monotonic()))
        if request.url.path == "/old":
            return httpx.Response(301, headers={"Location": "/new"})
        return httpx.Response(200, text="ok")

    async def go():
        async with fake_client(handler, interval=(0.3, 0.3)) as client:
            return await client.fetch("https://a.in/old")

    res = run(go())
    assert res.ok and res.redirects == 1 and res.final_url == "https://a.in/new"
    assert starts[1][2] - starts[0][2] >= 0.29


def test_never_fetches_x_instagram_or_whatsapp_even_through_a_redirect():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://x.com/someone"})

    async def go():
        async with fake_client(handler, retries=2) as client:
            return await client.fetch("https://t.co/abc"), await client.fetch("https://www.instagram.com/p/1")

    via_redirect, direct = run(go())
    assert via_redirect.error_kind == "blocked" and direct.error_kind == "blocked"
    assert seen == ["t.co"]  # x.com and instagram.com were never contacted, and nothing was retried


def test_too_many_redirects_is_an_error_not_a_loop():
    def handler(request):
        return httpx.Response(302, headers={"Location": "/again"})

    async def go():
        async with fake_client(handler, retries=2) as client:
            return await client.fetch("https://a.in/loop")

    res = run(go())
    assert res.error_kind == "too_many_redirects" and res.attempts == 1


async def no_wait(_seconds):
    return None


def test_a_dead_domain_is_skipped_after_three_failures_in_a_row(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        raise httpx.ConnectTimeout("timed out")

    async def go():
        async with fake_client(handler, retries=2) as client:
            monkeypatch.setattr(asyncio, "sleep", no_wait)
            first = await client.fetch("https://news.google.com/rss/search?q=a")
            second = await client.fetch("https://news.google.com/rss/search?q=b")
            other = await client.fetch("https://a.in/feed")
            return first, second, other

    first, second, other = run(go())
    assert first.error_kind == "timeout" and first.attempts == 3
    assert second.error_kind == "domain_down"
    assert calls.count("news.google.com") == 3 and calls.count("a.in") == 3  # other sites unaffected
    assert other.error_kind == "timeout"


def test_the_overall_deadline_counts_toward_a_dead_domain(monkeypatch):
    class Hang(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.Event().wait()  # never answers
            yield b""

    async def go():
        client = fake_client(lambda r: httpx.Response(200, stream=Hang()), retries=2)
        client.deadline = 0.05
        monkeypatch.setattr(asyncio, "sleep", no_wait)
        async with client:
            return await client.fetch("https://slow.in/1"), await client.fetch("https://slow.in/2")

    first, second = run(go())
    assert first.error_kind == "timeout" and second.error_kind == "domain_down"
