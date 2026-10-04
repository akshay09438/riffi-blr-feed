"""Step 2 (BRIEF.md "STEP 2: NORMALISE AND CLEAN"): turn fetched entries into clean items.

For every entry of every successful fetch:
- Google News: resolve the news.google.com link to the real article (fetchers/gnews.py); take the
  publisher from <source> and strip " - Publisher" from the title. A link the resolver cannot resolve
  this run is kept as the Google News link, marked url_resolved = False, and still checked against the
  blocklist through its <source> domain.
- one item shape (BRIEF.md "Data model"): title, url, canonical url, publisher, published_at (IST),
  fetched_at, summary, image_url, author, language, raw_guid, raw_payload;
- a missing date, or one more than 6 h in the future, becomes the fetch time (a lesson from the panel);
- language: "kn" when Kannada script dominates the title and summary, "en" otherwise ("other" when
  another script dominates);
- drop: no link, blocklisted domain (safety/blocklist.py), older than 7 days.

Every drop is counted with its reason, so health reports can show what was filtered and why.
Excluded-topic filtering needs the topic tags (step 4) and is not done here. Copied in part from the
panel project's fetcher/normalize.py (canonical_url, clean_title, norm_title - copied, not imported).
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .fetchers import gnews
from .fetchers.outcome import FetchOutcome
from .fetchers.parse import IST, Entry
from .safety.blocklist import Blocklist
from .sources import Source

MAX_AGE = timedelta(days=7)
FUTURE_TOLERANCE = timedelta(hours=6)
TELEGRAM = "RSSHub Telegram"
PAGE_MONITOR = "Web page monitor"
YOUTUBE = "YouTube Atom"

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid", "twclid", "li_fat_id", "mc_cid", "mc_eid",
    "igshid", "igsh", "ref", "ref_src", "ref_url", "cmpid", "s_cid", "ito", "_ga", "_gl", "ncid", "ocid", "oc",
    "rss", "amp", "fromrss", "spm", "share", "smid", "sr_share",
}  # fmt: skip
TRACKING_VALUES = {"from": {"rss", "feed", "rssfeed"}, "outputtype": {"amp"}, "source": {"rss", "feed"}}
HOST_ALIASES = {
    "m.economictimes.com": "economictimes.indiatimes.com",
    "m.timesofindia.com": "timesofindia.indiatimes.com",
    "m.thewire.in": "thewire.in",
    "m.hindustantimes.com": "www.hindustantimes.com",
}
WS_RE = re.compile(r"\s+")
KANNADA_RE = re.compile(r"[ಀ-೿]")
LATIN_RE = re.compile(r"[A-Za-z]")
OTHER_LETTER_RE = re.compile(r"[^\W\d_A-Za-zಀ-೿]")


def canonical_url(url: str) -> str:
    """One spelling per article: https, no www-less/amp/mobile variants, no tracking parameters."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:  # e.g. "http://[::1" or a port of 99999: keep the link as it is
        return url.strip()
    if not parts.scheme or not parts.netloc:
        return url.strip()
    host = HOST_ALIASES.get(host, host)
    if host.startswith("amp."):
        host = host[4:]
    netloc = host if not port or port in (80, 443) else f"{host}:{port}"

    path = unquote(parts.path) if "%2f" in parts.path.lower() else parts.path
    path = re.sub(r"^/amp/story(?=/)", "", path)
    path = re.sub(r"^/amp(?=/)", "", path)
    path = path.replace("/amp_articleshow/", "/articleshow/").replace("/amp_news/", "/news/")
    path = re.sub(r"/amp/?$", "", path)
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")

    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        k = key.lower()
        if k.startswith("utm_") or k in TRACKING_PARAMS:
            continue
        if value.lower() in TRACKING_VALUES.get(k, ()):
            continue
        query.append((key, value))
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    return urlunsplit((scheme, netloc, path or "/", urlencode(sorted(query)), ""))


def item_id(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def clean_title(title: str, publisher: str = "") -> str:
    """Drop the ' - Publisher' suffix Google News appends."""
    title = WS_RE.sub(" ", title or "").strip()
    if publisher and title.endswith(f" - {publisher}"):
        title = title[: -len(publisher) - 3].rstrip()
    return title


def norm_title(title: str) -> str:
    """Lower case, no punctuation: the form de-duplication compares (step 3). Letters, digits and
    combining marks are kept - Kannada vowel signs are marks, and dropping them breaks words apart."""
    kept = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in (title or "").lower())
    return WS_RE.sub(" ", kept).strip()


KANNADA_SHARE = 0.3  # "Namma Metro ಹಳದಿ ಮಾರ್ಗ" is a Kannada headline; one Kannada word in English is not


def detect_language(text: str) -> str:
    kannada, latin = len(KANNADA_RE.findall(text)), len(LATIN_RE.findall(text))
    other = len(OTHER_LETTER_RE.findall(text))
    total = kannada + latin + other
    if total and kannada / total >= KANNADA_SHARE:
        return "kn"
    if other > latin:
        return "other"
    return "en"


@dataclass
class Item:
    item_id: str
    source_id: str
    title: str
    url: str  # the article URL (resolved for Google News when possible)
    canonical_url: str
    publisher: str
    published_at: datetime  # IST
    fetched_at: datetime  # IST
    summary: str = ""
    image_url: str = ""
    author: str = ""
    language: str = "en"
    raw_guid: str = ""
    raw_payload: dict = field(default_factory=dict)
    url_resolved: bool = True  # False: a Google News link the resolver could not resolve this run
    on_backup: bool = False  # from an X/Instagram row read through its backup Google News query


@dataclass
class CleanResult:
    items: list[Item] = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)  # reason -> count


def _gnews_id(link: str) -> str | None:
    """The Google News article id in `link`, or None (also for a link too broken to read)."""
    try:
        return gnews.article_id(link)
    except ValueError:
        return None


def _payload(entry: Entry) -> dict:
    data = asdict(entry)
    data["published"] = entry.published.isoformat() if entry.published else None
    return data


def _publisher(entry: Entry, source: Source, outcome: FetchOutcome, from_gnews: bool) -> str:
    if from_gnews:
        return entry.source_title or source.name
    if source.route_type in (TELEGRAM, YOUTUBE):
        return outcome.feed_title or source.name  # channel name
    return source.name


async def _resolve_all(entries: list[Entry], resolver: gnews.Resolver | None) -> dict[str, str | None]:
    links = sorted({e.link for e in entries if _gnews_id(e.link)})
    if not links or resolver is None:
        return {}
    answers = await asyncio.gather(*(resolver.resolve(link) for link in links))
    return {link: url for link, (url, _how) in zip(links, answers, strict=True)}


def _published(entry: Entry, now: datetime) -> datetime:
    published = entry.published
    if published is not None and published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)  # Entry dates are UTC by contract
    return now if published is None or published > now + FUTURE_TOLERANCE else published


def _blocked_before_resolving(entry: Entry, blocklist: Blocklist) -> str | None:
    """Check everything already known: the link, and for Google News the <source> URL and name
    (a name can be the publisher's domain, e.g. "bengalurumetro.in")."""
    name = entry.source_title or ""
    return (
        blocklist.match(entry.link)
        or blocklist.match(entry.source_url or "")
        or ("." in name and blocklist.match(name))
        or None
    )


def _make_item(
    outcome: FetchOutcome, entry: Entry, source: Source, resolved: dict, blocklist: Blocklist, now: datetime
) -> Item | str:
    """The clean item, or the reason it is dropped."""
    link = entry.link.strip()
    from_gnews = bool(_gnews_id(link))
    url = (resolved.get(link) or link) if from_gnews else link
    url_resolved = not from_gnews or url != link
    if from_gnews and (hit := blocklist.match(url)):  # the resolved article is on a blocklisted site
        return f"blocklisted ({hit})"
    if from_gnews and not url_resolved and not (entry.source_url or "." in (entry.source_title or "")):
        # nothing says which site this is, so the blocklist cannot be checked: hold it back until a later
        # run resolves the link, rather than risk letting a copycat site through
        return "google news link not resolved and publisher site unknown"
    summary_text = entry.summary or ""
    title = clean_title(entry.title or "", entry.source_title if from_gnews else "")
    if not title and source.route_type == TELEGRAM:
        title = summary_text.split(". ")[0][:200]  # a Telegram post's first line
    summary = "" if summary_text.strip() == title else summary_text
    canon = canonical_url(url)
    # a page monitor reports every change of one page under the same URL; its guid tells changes apart
    identity = f"{canon}#{entry.guid}" if source.route_type == PAGE_MONITOR and entry.guid else canon
    return Item(
        item_id=item_id(identity),
        source_id=source.source_id,
        title=title,
        url=url,
        canonical_url=canon,
        publisher=_publisher(entry, source, outcome, from_gnews),
        published_at=_published(entry, now).astimezone(IST),
        fetched_at=now.astimezone(IST),
        summary=summary,
        image_url=entry.image_url or "",
        author=entry.author or "",
        language=detect_language(f"{title} {summary}"),
        raw_guid=entry.guid or "",
        raw_payload=_payload(entry),
        url_resolved=url_resolved,
        on_backup=outcome.on_backup,
    )


async def clean(
    outcomes: list[FetchOutcome],
    sources: dict[str, Source],
    *,
    blocklist: Blocklist,
    resolver: gnews.Resolver | None,
    now: datetime | None = None,
) -> CleanResult:
    """Clean every entry of every successful fetch. `sources` is keyed by source_id. Never raises for
    an odd entry: it is dropped with the reason.

    The cheap checks (link, age, blocklist on what is already known) run before Google News links are
    resolved, so the per-run cap on online look-ups is never spent on items that would be dropped."""
    now = now or datetime.now(timezone.utc)
    result = CleanResult()
    kept: list[tuple[FetchOutcome, Entry]] = []
    for outcome in outcomes:
        if outcome.status != "ok":
            continue
        for entry in outcome.entries:
            try:
                if not isinstance(entry.link, str) or not entry.link.strip():
                    result.dropped["no link"] += 1
                elif outcome.source_id not in sources:
                    result.dropped["unknown source"] += 1
                elif now - _published(entry, now) > MAX_AGE:
                    result.dropped["older than 7 days"] += 1
                elif hit := _blocked_before_resolving(entry, blocklist):
                    result.dropped[f"blocklisted ({hit})"] += 1
                else:
                    kept.append((outcome, entry))
            except Exception as exc:  # one odd entry must not stop the run
                result.dropped[f"could not read entry ({type(exc).__name__})"] += 1
    resolved = await _resolve_all([e for _, e in kept], resolver)
    for outcome, entry in kept:
        try:
            item = _make_item(outcome, entry, sources[outcome.source_id], resolved, blocklist, now)
        except Exception as exc:  # one odd entry must not stop the run
            result.dropped[f"could not read entry ({type(exc).__name__})"] += 1
            continue
        if isinstance(item, Item):
            result.items.append(item)
        else:
            result.dropped[item] += 1
    return result
