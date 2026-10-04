"""The one HTTP client every fetcher goes through. Dangerous file (CLAUDE.md item 8): the identity
we send and how politely we fetch are decided here, and a block by a publisher is not undone by
reverting code.

Rules enforced here, so no caller can forget them (BRIEF.md step 1, D-004):
- browser-like User-Agent (D-004: the founder chose this over the panel project's bot identity)
- one request at a time per domain, and at least 2 s between request starts on the same domain -
  every redirect hop counts as a request on its own domain
- never X, Instagram or WhatsApp, not even through a redirect (BRIEF.md hard rule)
- 20 s timeout per read plus a 60 s deadline per attempt (redirects included)
- up to 2 retries with exponential backoff on network errors, 429 and 5xx (Retry-After honoured, capped);
  no retry for a DNS, TLS or bad-URL failure
- a domain that fails 3 times in a row at network level is skipped for the rest of the run, so a dead
  host (say news.google.com, with 84 queued feeds) cannot stretch one run into hours
- conditional GET: send the last ETag / Last-Modified, and report a 304 as "not modified"
- TLS certificates are always verified; a certificate problem is a finding, never something to switch off

Adapted from the panel project's feedkit/net.py at commit 0a07e29 (copied, not imported - D-006).
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
FEED_ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.9, */*;q=0.8"
PAGE_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

TIMEOUT = 20.0
DEADLINE = 60.0  # per attempt; some servers (NSE) drip bytes so slowly that the per-read timeout never fires
RETRIES = 2
DOMAIN_INTERVAL = (2.0, 2.5)  # seconds between request starts on one domain: at least 2 s, a little jitter
MAX_REDIRECTS = 10
MAX_BYTES = 15 * 1024 * 1024
RETRY_STATUSES = {429, 500, 502, 503, 504}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_RETRY_AFTER = 30.0
DOMAIN_FAILURE_LIMIT = 3  # consecutive network-level failures before a domain is skipped for the run
NO_RETRY_KINDS = {"dns", "tls", "too_large", "bad_url", "blocked", "domain_down", "too_many_redirects"}
NETWORK_KINDS = {"timeout", "dns", "connect"}  # failures that say the host itself is unreachable

# BRIEF.md: never scrape X, Instagram or WhatsApp directly.
BLOCKED_DOMAINS = ("x.com", "twitter.com", "instagram.com", "whatsapp.com", "whatsapp.net", "wa.me")


def domain_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_blocked(url: str) -> bool:
    host = domain_key(url)
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int | None = None
    content_type: str = ""
    content: bytes = b""
    headers: dict = field(default_factory=dict)
    error: str | None = None
    error_kind: str | None = None  # see NO_RETRY_KINDS / NETWORK_KINDS, plus "other"
    attempts: int = 0
    redirects: int = 0
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    @property
    def not_modified(self) -> bool:
        return self.error is None and self.status == 304

    @property
    def etag(self) -> str:
        return self.headers.get("etag", "")

    @property
    def last_modified(self) -> str:
        return self.headers.get("last-modified", "")

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def fail(self, kind: str, message: str) -> None:
        self.status, self.content, self.headers = None, b"", {}
        self.error_kind, self.error = kind, message[:300]


class _Gate:
    """One request at a time per domain, with a minimum gap between request starts."""

    def __init__(self, interval: tuple[float, float]):
        self.lock = asyncio.Lock()
        self.interval = interval
        self.next_start = 0.0
        self.failures = 0  # consecutive network-level failures

    async def __aenter__(self):
        await self.lock.acquire()
        try:
            now = time.monotonic()
            if self.next_start > now:
                await asyncio.sleep(self.next_start - now)
        except BaseException:  # cancelled while waiting its turn: free the domain for everyone else
            self.lock.release()
            raise
        # the gap counts from the start of this request, so a slow answer does not add to it
        self.next_start = time.monotonic() + random.uniform(*self.interval)
        return self

    async def __aexit__(self, *exc):
        self.lock.release()


def _classify_error(exc: Exception) -> str:
    msg = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, (httpx.InvalidURL, httpx.UnsupportedProtocol)):
        return "bad_url"
    if any(s in msg for s in ("getaddrinfo", "name or service not known", "nodename nor servname", "no address")):
        return "dns"
    if "ssl" in msg or "certificate" in msg or "tls" in msg:
        return "tls"
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError)):
        return "connect"
    return "other"


def _retry_after(headers: dict) -> float | None:
    try:
        value = float(headers.get("retry-after", ""))
    except ValueError:
        return None  # an HTTP date or junk: use the normal backoff
    if not math.isfinite(value) or value < 0:
        return None
    return min(value, MAX_RETRY_AFTER)


class _TooLarge(Exception):
    pass


class PoliteClient:
    """Use as `async with PoliteClient() as client: await client.fetch(url)`. Never raises for a
    network or HTTP problem: the FetchResult carries the error instead."""

    def __init__(
        self,
        *,
        timeout: float = TIMEOUT,
        deadline: float = DEADLINE,
        retries: int = RETRIES,
        interval: tuple[float, float] = DOMAIN_INTERVAL,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.deadline = deadline
        self.retries = retries
        self.interval = interval
        self._gates: dict[str, _Gate] = {}
        # redirects are followed by hand in _attempt, so each hop goes through its own domain's gate
        self._client = httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(timeout), transport=transport)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        await self._client.aclose()

    def _gate(self, url: str) -> _Gate:
        key = domain_key(url)
        if key not in self._gates:
            self._gates[key] = _Gate(self.interval)
        return self._gates[key]

    async def fetch(
        self,
        url: str,
        *,
        accept: str = FEED_ACCEPT,
        etag: str = "",
        last_modified: str = "",
        headers: dict | None = None,
        method: str = "GET",
        data: str | bytes | None = None,
        retries: int | None = None,
    ) -> FetchResult:
        hdrs = {"User-Agent": BROWSER_UA, "Accept": accept, "Accept-Language": "en-IN,en;q=0.9"}
        if etag:
            hdrs["If-None-Match"] = etag
        if last_modified:
            hdrs["If-Modified-Since"] = last_modified
        if headers:
            hdrs.update(headers)
        result = FetchResult(url=url)
        max_attempts = 1 + (self.retries if retries is None else retries)
        started = time.monotonic()
        for attempt in range(max_attempts):
            result.attempts = attempt + 1
            try:
                async with asyncio.timeout(self.deadline):
                    await self._attempt(result, method, url, hdrs, data)
            except TimeoutError:
                result.fail("timeout", f"no complete answer within {self.deadline:.0f}s")
                self._gate(result.final_url or url).failures += 1
            if result.error_kind in NO_RETRY_KINDS:
                break
            if result.error is None and result.status not in RETRY_STATUSES:
                break
            if attempt + 1 < max_attempts:
                wait = _retry_after(result.headers) if result.status == 429 else None
                await asyncio.sleep(wait if wait is not None else 2.0 * (2**attempt))
        result.elapsed = round(time.monotonic() - started, 2)
        return result

    async def _attempt(self, result: FetchResult, method: str, url: str, hdrs: dict, data) -> None:
        result.redirects = 0
        for _hop in range(MAX_REDIRECTS + 1):
            if is_blocked(url):
                return result.fail("blocked", f"refused: {domain_key(url)} is never fetched (no X/Instagram/WhatsApp)")
            result.final_url = url  # the hop in flight, so a timeout is charged to the right domain
            gate = self._gate(url)
            if gate.failures >= DOMAIN_FAILURE_LIMIT:
                return result.fail("domain_down", f"{domain_key(url)} failed {gate.failures} times in a row this run")
            async with gate:
                await self._send(result, gate, method, url, hdrs, data)
            location = result.headers.get("location")
            if result.error or result.status not in REDIRECT_STATUSES or not location:
                return None
            url = urljoin(url, location)
            result.redirects += 1
            if result.status == 303 or (result.status in (301, 302) and method == "POST"):
                method, data = "GET", None
        return result.fail("too_many_redirects", f"more than {MAX_REDIRECTS} redirects")

    async def _send(self, result: FetchResult, gate: _Gate, method: str, url: str, hdrs: dict, data) -> None:
        try:
            async with self._client.stream(method, url, headers=hdrs, content=data) as resp:
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise _TooLarge()
                    chunks.append(chunk)
                result.status = resp.status_code
                result.final_url = str(resp.url)
                result.headers = {k.lower(): v for k, v in resp.headers.items()}
                result.content_type = result.headers.get("content-type", "")
                result.content = b"".join(chunks)
                result.error = result.error_kind = None
            gate.failures = 0
        except _TooLarge:
            result.fail("too_large", f"response larger than {MAX_BYTES} bytes")
        except Exception as exc:  # any network-level failure becomes a finding, never a crash
            result.fail(_classify_error(exc), f"{type(exc).__name__}: {exc}")
            if result.error_kind in NETWORK_KINDS:
                gate.failures += 1
