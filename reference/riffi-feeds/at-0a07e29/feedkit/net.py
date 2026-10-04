"""Polite async HTTP for feed work.

Rules enforced here, so no caller can forget them:
- at most 2 requests in flight per domain
- 1-2s (randomised) between request starts on the same domain; slower lanes for
  hosts that rate-limit hard (Reddit)
- 20s timeout, redirects followed, up to 2 retries with backoff on network errors,
  429 and 5xx (Retry-After honoured, capped)
- TLS certificates are always verified; a certificate problem is a finding, never
  something to switch off
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

BOT_TOKEN = "RiffiFeedBot"
PLACEHOLDER_MARKERS = ("<", "your-", "example")


def contact_is_placeholder(contact: str) -> bool:
    c = (contact or "").strip().lower()
    return not c or any(m in c for m in PLACEHOLDER_MARKERS)


def bot_user_agent(contact: str = "") -> str:
    """Our bot's name tag. A contact is only sent once it is a real address, never a placeholder."""
    if contact_is_placeholder(contact):
        return f"{BOT_TOKEN}/1.0 (news feed reader; contact pending)"
    contact = contact.strip()
    return f"{BOT_TOKEN}/1.0 (+mailto:{contact})" if "@" in contact and ":" not in contact else f"{BOT_TOKEN}/1.0 (+{contact})"


def set_contact(contact: str) -> str:
    """Set the contact every later request carries (from config defaults.bot_contact)."""
    global BOT_UA
    BOT_UA = bot_user_agent(contact)
    return BOT_UA


BOT_UA = bot_user_agent(os.environ.get("FEEDBOT_CONTACT", ""))
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
FEED_ACCEPT = (
    "application/rss+xml, application/atom+xml, application/xml;q=0.9, "
    "text/xml;q=0.9, */*;q=0.8"
)
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

MAX_BYTES = 15 * 1024 * 1024
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER = 30.0

# (min, max) seconds between request starts, per domain group
DEFAULT_INTERVAL = (1.0, 2.0)
SLOW_LANES = {
    # Reddit (logged-out RSS, undocumented limits), 27 Sep 2026: 429 after 2 requests 10s apart,
    # and again after 27 requests at 55-80s apart (~52/hour). 70-100s apart caps us near 42 an
    # hour, just above the 37.9 an hour the polling tiers need.
    "reddit.com": (70.0, 100.0),
    "api.github.com": (1.0, 2.0),
}
LANE_CONCURRENCY = {"reddit.com": 1}  # Reddit: strictly one request at a time


def domain_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for group in SLOW_LANES:
        if host == group or host.endswith("." + group):
            return group
    return host


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int | None = None
    content_type: str = ""
    content: bytes = b""
    headers: dict = field(default_factory=dict)
    error: str | None = None
    error_kind: str | None = None  # timeout | dns | tls | connect | too_large | other
    ua: str = "bot"
    attempts: int = 0
    redirects: int = 0
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class _Gate:
    """Per-domain concurrency cap plus minimum spacing between request starts."""

    def __init__(self, max_concurrent: int, interval: tuple[float, float]):
        self.sem = asyncio.Semaphore(max_concurrent)
        self.lock = asyncio.Lock()
        self.interval = interval
        self.next_start = 0.0

    async def __aenter__(self):
        await self.sem.acquire()
        async with self.lock:
            now = time.monotonic()
            if self.next_start > now:
                await asyncio.sleep(self.next_start - now)
            self.next_start = time.monotonic() + random.uniform(*self.interval)
        return self

    async def __aexit__(self, *exc):
        self.sem.release()


def _classify_error(exc: Exception) -> str:
    msg = f"{type(exc).__name__}: {exc}".lower()
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if "getaddrinfo" in msg or "name or service not known" in msg or "nodename nor servname" in msg or "no address" in msg:
        return "dns"
    if "ssl" in msg or "certificate" in msg or "tls" in msg:
        return "tls"
    if isinstance(exc, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError)):
        return "connect"
    return "other"


def _retry_after(headers: dict) -> float | None:
    value = headers.get("retry-after") or headers.get("x-ratelimit-reset")  # Reddit sends the latter
    if not value:
        return None
    try:
        return min(float(value), MAX_RETRY_AFTER)
    except ValueError:
        return None


class PoliteClient:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        deadline: float = 60.0,
        retries: int = 2,
        max_per_domain: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.retries = retries
        self.deadline = deadline
        self.max_per_domain = max_per_domain
        self._gates: dict[str, _Gate] = {}
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=10,
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self):
        await self._client.aclose()

    def _gate(self, url: str) -> _Gate:
        key = domain_key(url)
        gate = self._gates.get(key)
        if gate is None:
            gate = _Gate(LANE_CONCURRENCY.get(key, self.max_per_domain), SLOW_LANES.get(key, DEFAULT_INTERVAL))
            self._gates[key] = gate
        return gate

    async def fetch(
        self,
        url: str,
        *,
        ua: str = "bot",
        headers: dict | None = None,
        method: str = "GET",
        data: str | bytes | None = None,
        retries: int | None = None,
    ) -> FetchResult:
        hdrs = {
            "User-Agent": BOT_UA if ua == "bot" else BROWSER_UA,
            "Accept": FEED_ACCEPT if ua == "bot" else BROWSER_ACCEPT,
            "Accept-Language": "en-IN,en;q=0.9",
        }
        if headers:
            hdrs.update(headers)
        result = FetchResult(url=url, ua=ua)
        max_attempts = 1 + (self.retries if retries is None else retries)
        started = time.monotonic()
        for attempt in range(max_attempts):
            result.attempts = attempt + 1
            wait = None
            async with self._gate(url):
                try:
                    # an overall deadline: some servers (NSE for bots) drip bytes so slowly
                    # that the per-read timeout never fires
                    async with asyncio.timeout(self.deadline), self._client.stream(method, url, headers=hdrs, content=data) as resp:
                        chunks, size = [], 0
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise _TooLarge()
                            chunks.append(chunk)
                        result.status = resp.status_code
                        result.final_url = str(resp.url)
                        result.redirects = len(resp.history)
                        result.headers = {k.lower(): v for k, v in resp.headers.items()}
                        result.content_type = result.headers.get("content-type", "")
                        result.content = b"".join(chunks)
                        result.error = result.error_kind = None
                except _TooLarge:
                    result.error, result.error_kind = f"response larger than {MAX_BYTES} bytes", "too_large"
                    break
                except TimeoutError:
                    result.status = None
                    result.error = f"no complete answer within {self.deadline:.0f}s"
                    result.error_kind = "timeout"
                except Exception as exc:  # network-level failure
                    result.status = None
                    result.error = f"{type(exc).__name__}: {exc}"[:300]
                    result.error_kind = _classify_error(exc)
            if result.error_kind in ("dns", "tls"):
                break  # retrying will not change a DNS or certificate failure
            if result.error is None and result.status not in RETRY_STATUSES:
                break
            if attempt + 1 < max_attempts:
                wait = _retry_after(result.headers) if result.status == 429 else None
                await asyncio.sleep(wait if wait is not None else 2.0 * (2**attempt))
        result.elapsed = round(time.monotonic() - started, 2)
        return result


class _TooLarge(Exception):
    pass


# ---------------------------------------------------------------- robots.txt


def _pattern_regex(pattern: str) -> re.Pattern:
    anchored = pattern.endswith("$")
    if anchored:
        pattern = pattern[:-1]
    body = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.compile(body + ("$" if anchored else ""))


@dataclass
class Robots:
    """RFC 9309 robots.txt: most specific group wins, longest matching rule wins,
    Allow wins a tie."""

    fetched_status: int | None = None
    note: str = ""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = field(default_factory=list)
    sitemaps: list[str] = field(default_factory=list)
    allow_all: bool = False
    unreachable: bool = False

    @classmethod
    def parse(cls, text: str, status: int | None = 200) -> "Robots":
        robots = cls(fetched_status=status)
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "sitemap":
                if value:
                    robots.sitemaps.append(value)
            elif key == "user-agent":
                if in_rules:
                    robots.groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif key in ("allow", "disallow"):
                in_rules = True
                if value:
                    rules.append((key == "allow", value))
        if agents:
            robots.groups.append((agents, rules))
        return robots

    def allowed(self, url: str, token: str = BOT_TOKEN) -> bool | None:
        if self.unreachable:
            return None
        if self.allow_all:
            return True
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        token = token.lower()
        chosen = [r for a, r in self.groups if token in a]
        if not chosen:
            chosen = [r for a, r in self.groups if "*" in a]
        rules = [rule for group in chosen for rule in group]
        best_len, verdict = -1, True
        for is_allow, pattern in rules:
            if _pattern_regex(pattern).match(path):
                plen = len(pattern)
                if plen > best_len or (plen == best_len and is_allow):
                    best_len, verdict = plen, is_allow
        return verdict


class RobotsCache:
    def __init__(self, client: PoliteClient):
        self.client = client
        self._cache: dict[str, Robots] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, url: str) -> Robots:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin in self._cache:
                return self._cache[origin]
            res = await self.client.fetch(origin + "/robots.txt", retries=1)
            if res.ok and b"<html" not in res.content[:2000].lower():
                robots = Robots.parse(res.text, res.status)
            elif res.status is not None and 400 <= res.status < 500:
                robots = Robots(fetched_status=res.status, allow_all=True, note=f"no robots.txt ({res.status})")
            elif res.ok:
                robots = Robots(fetched_status=res.status, allow_all=True, note="robots.txt served HTML")
            else:
                robots = Robots(
                    fetched_status=res.status,
                    unreachable=True,
                    note=f"robots.txt unreachable ({res.status or res.error_kind})",
                )
            self._cache[origin] = robots
            return robots

    async def allowed(self, url: str) -> tuple[str, str]:
        robots = await self.get(url)
        verdict = robots.allowed(url)
        label = {True: "yes", False: "no", None: "unknown"}[verdict]
        return label, robots.note
