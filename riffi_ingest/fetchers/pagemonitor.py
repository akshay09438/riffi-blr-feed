"""Web page monitors (BRIEF.md step 1): for the 22 government, ticketing and data pages with no feed.

GET the page, pull out its main text with trafilatura, and compare it with the snapshot from last time.
When the text really changed, emit one entry: title = page title + " updated", link = the page,
published = when the change was seen, summary = what was added and removed.

What does not count as a change:
- whitespace, and lines only moving around (the comparison is on the set of lines);
- "stamp" lines: a line that names a page stamp ("Last updated", "Visitors", "Sunrise", "All rights
  reserved"...) and otherwise holds only dates, times, numbers and filler words - plus a bare date/time
  line right after such a line ("Last updated:" / "04/10/2026");
- a bot-check page (Cloudflare "Just a moment...", "Access denied"): that is an error, not new content;
- site-template policy text: before extraction, the page loses its privacy / disclaimer / terms /
  hyperlinking / copyright-policy blocks (an element whose id or class names one, a hidden modal or
  popup whose heading is one, or a policy heading and everything after it in its section), so
  trafilatura cannot take the karnataka.gov.in privacy-policy modal for the page's main text;
- a JavaScript app shell ("You need to enable JavaScript to run this app."): <noscript> and "enable
  JavaScript" lines are dropped, and a page left with no text is an error ("needs JavaScript"), which
  keeps the last snapshot and gets the health check's JavaScript fix.

The karnataka.gov.in template (S004 CM, S048, S109 BMTC, S110 BWSSB) is read differently. Its pages carry
about 20 hidden modals (policies, help, link lists) and trafilatura picks one of those, or a block of photo
captions, as the main text. On this template the news lists are the page: the "latest news" modal
(#newsModal) with its "Quick Announcements" (#exampleModal), or the visible "News and Events" list
(section.news_container). When a page has one, its items are the page's lines and trafilatura is not
used. Each item loses its list number ("1:") and its relative age ("4 months ago"), which changes by
itself every month.

A previous snapshot that was itself blind (only policy text, or only a JavaScript notice - taken before
these rules) is replaced without an "updated" item, so the first visit after the fix is a new baseline.
Such a snapshot is fetched without the conditional-GET validators too: a 304 would otherwise keep it
forever (S008 and S009 did, 5 Oct).

Dates inside real content still count ("effective from 10 October" becoming "15 October"), and so do
lines of numbers or dates without a stamp word (table rows, "Date: 15/10/2026"), and any line with
Kannada text.

The first visit to a page only records a snapshot. On any error the previous snapshot is handed back
unchanged. Storing snapshots between runs is step 6 (page_snapshots); this module takes the previous
one in and hands the new one back on the outcome.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import re
import time
from datetime import datetime, timezone

import trafilatura
from lxml import html as lxml_html
from trafilatura.utils import decode_file, load_html

from ..sources import Source
from .http import PAGE_ACCEPT, PoliteClient
from .outcome import FetchOutcome, PageSnapshot, Validators
from .parse import WS_RE, Entry, looks_like_html

# trafilatura slows down sharply on big pages (about 6 s at 1 MB, minutes at a few MB), so pages are cut
# at 1 MB before extraction, and extraction runs in a worker thread with a time limit.
MAX_EXTRACT_BYTES = 1024 * 1024
EXTRACT_TIMEOUT = 60.0
SUMMARY_CHARS = 1000
MIN_LINE_CHARS = 3

HEAD_TITLE_RE = re.compile(r"<head\b.*?<title[^>]*>(.*?)</title>", re.I | re.S)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
DATE_TIME_RE = re.compile(
    r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"  # 04/10/2026, 04.10.2026, 4-10-26
    r"|\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?\b"  # 2026-10-04, 2026-10-04T10:32
    r"|\b\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?|hrs|ist)?(?![\d.])"  # 10:32, 10:32 AM, 10.32 hrs
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?[\s-]+{MONTH},?[\s-]+\d{{4}}\b"  # 4 October 2026, 04-Oct-2026
    rf"|\b{MONTH}\s+\d{{1,2}},?\s+\d{{4}}\b",  # October 4, 2026
    re.I,
)
WORD_RE = re.compile(r"[^\W\d_]+")  # letters in any script, so Kannada words count as words
# A stamp line must contain one of these...
STAMP_ANCHORS = {
    "updated", "modified", "reviewed", "visitors", "visitor", "visits", "views", "hits", "counter",
    "copyright", "reserved", "sunrise", "sunset", "moonrise", "moonset", "ago",
}  # fmt: skip
# ...and every other word must be filler like these.
STAMP_FILLER = STAMP_ANCHORS | {
    "last", "update", "on", "as", "at", "and", "date", "time", "page", "site", "website", "count", "total",
    "today", "ist", "am", "pm", "hrs", "of", "the", "content", "all", "rights", "by", "no", "is", "was",
    "minutes", "minute", "mins", "hours", "hour", "seconds", "days", "day", "just", "now",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
}  # fmt: skip
# Bot-check pages: matched on the <title>, and on markup only a challenge page carries.
BOT_CHECK_TITLES = ("just a moment", "attention required", "access denied", "are you a robot", "security check")
BOT_CHECK_MARKUP = ("cf-chl-", "challenge-platform", "cf_chl_opt", "verify you are human")
# Site-template policy blocks (karnataka.gov.in pages carry a Kannada/English privacy policy in a hidden
# modal). Bare "policy" is not on the list: a department's "Policies" list is real content.
POLICY_ATTR_RE = re.compile(
    r"privacy|disclaimer|hyperlink(?:ing)?[-_ ]?polic|terms[-_ ]?(?:of|and|&|conditions|use|service)|copyright[-_ ]?polic|website[-_ ]?polic",
    re.I,
)
POLICY_TITLE_RE = re.compile(
    r"^(?:website\s+)?(?:privacy\s+(?:policy|statement|notice)|disclaimer|terms\s*(?:of\s+(?:use|service)|(?:and|&)\s*conditions)"
    r"|hyperlink(?:ing)?\s+policy|copyright\s+policy|website\s+policies"
    r"|ಗೌಪ್ಯತಾ\s*ನೀತಿ|ಗೌಪ್ಯತೆ\s*ನೀತಿ|ಖಾಸಗಿತನ\s*ನೀತಿ|ಹಕ್ಕು\s*ನಿರಾಕರಣೆ|ಹಕ್ಕುತ್ಯಾಗ|ಹೈಪರ್\s*ಲಿಂಕ್\s*ನೀತಿ|ಹೈಪರ್ಲಿಂಕ್\s*ನೀತಿ"
    r"|ಹಕ್ಕುಸ್ವಾಮ್ಯ\s*ನೀತಿ|ನಿಯಮಗಳು\s*ಮತ್ತು\s*ಷರತ್ತುಗಳು|ಬಳಕೆಯ\s*ನಿಯಮಗಳು)",
    re.I,
)
MAX_POLICY_TITLE_CHARS = 60
POLICY_MENTION_RE = re.compile(r"privacy policy|ಗೌಪ್ಯತಾ ನೀತಿ|ಗೌಪ್ಯತೆ ನೀತಿ|personal information|ವೈಯಕ್ತಿಕ ಮಾಹಿತಿ", re.I)
HIDDEN_ATTR_RE = re.compile(r"modal|popup|pop-up|lightbox|overlay|dialog", re.I)
HIDDEN_STYLE_RE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
# "You need to enable JavaScript to run this app.", "Please enable JavaScript", "JavaScript is required"
JS_NOTICE_RE = re.compile(
    r"\b(?:enable|turn on|switch on|requires?|required|needs?|activate)\b.{0,40}\bjavascript\b"
    r"|\bjavascript\b.{0,40}\b(?:enabled?|required|disabled|turned off|not supported)\b",
    re.I,
)
MAX_JS_NOTICE_CHARS = 160
# karnataka.gov.in template news lists (rules in the module docstring); #exampleModal is Bootstrap's demo id,
# so it only counts on a page that has one of the others
NEWS_BLOCK_XPATH = (
    '//*[@id="newsModal"] | //section[contains(concat(" ", normalize-space(@class), " "), " news_container ")]'
)
NEWS_EXTRA_XPATH = '//*[@id="exampleModal"]'
NEWS_ITEM_TAGS = ("p", "li", "td", *HEADING_TAGS)
ITEM_NUMBER_RE = re.compile(r"^\d{1,3}\s*:\s*")
AGE_TAIL_RE = re.compile(r"\s*\b(?:\d+|an?|one)\s+(?:second|minute|hour|day|week|month|year)s?\s+ago$", re.I)
NEEDS_JS_REASON = "the page needs JavaScript (only an app shell or no text came back); kept the last snapshot"


def _words(line: str) -> list[str]:
    return WORD_RE.findall(DATE_TIME_RE.sub(" ", line.lower()))


def is_stamp_line(line: str) -> bool:
    """True for "Last Reviewed and Updated on : 04 Oct 2026", "Visitors: 12345", "Updated 5 minutes ago",
    "Sunrise 06:10 Sunset 18:05". False for content, including "Date: 15/10/2026" and number rows."""
    words = _words(line)
    return bool(words) and any(w in STAMP_ANCHORS for w in words) and all(w in STAMP_FILLER for w in words)


def _is_bare_date_time(line: str) -> bool:
    return not _words(line) and bool(DATE_TIME_RE.search(line)) and "|" not in line


def page_title(page_html: str) -> str:
    m = HEAD_TITLE_RE.search(page_html)
    return WS_RE.sub(" ", html.unescape(m.group(1))).strip() if m else ""


def is_bot_check(title: str, page_html: str) -> bool:
    t, head = title.lower(), page_html[:20000].lower()
    return any(m in t for m in BOT_CHECK_TITLES) or any(m in head for m in BOT_CHECK_MARKUP)


def is_policy_title(text: str) -> bool:
    """True for a policy heading: "Privacy Policy", "ಗೌಪ್ಯತಾ ನೀತಿ", "Hyperlinking Policy", "Disclaimer"."""
    t = WS_RE.sub(" ", text.replace("\u200c", "").replace("\u200d", "")).strip(" :-|\u2013")
    return 0 < len(t) <= MAX_POLICY_TITLE_CHARS and bool(POLICY_TITLE_RE.fullmatch(t))


def is_js_notice(line: str) -> bool:
    """True for a JavaScript app shell's only text: "You need to enable JavaScript to run this app."."""
    return len(line) <= MAX_JS_NOTICE_CHARS and bool(JS_NOTICE_RE.search(line))


def _attrs(el) -> str:
    return f"{el.get('id', '')} {el.get('class', '')} {el.get('role', '')}"


def _is_hidden(el) -> bool:
    return (
        el.get("hidden") is not None
        or el.get("aria-hidden", "").lower() == "true"
        or bool(HIDDEN_STYLE_RE.search(el.get("style", "")))
        or bool(HIDDEN_ATTR_RE.search(_attrs(el)))
    )


def _first_heading(el):
    for h in el.iter(*HEADING_TAGS, "strong", "b"):
        if h.text_content().strip():
            return h
    return None


def _drop(el) -> None:
    parent = el.getparent()
    if parent is not None:
        el.drop_tree()


def strip_template_blocks(tree) -> None:
    """Remove policy blocks and <noscript> notices in place (rules in the module docstring)."""
    for el in list(tree.iter("noscript")):
        _drop(el)
    for el in list(tree.iter()):
        if not isinstance(el.tag, str) or el.getparent() is None or el.tag in ("html", "body", "a"):
            continue
        if POLICY_ATTR_RE.search(_attrs(el)):
            _drop(el)
        elif _is_hidden(el) and (h := _first_heading(el)) is not None and is_policy_title(h.text_content()):
            _drop(el)
    # a policy printed in the page itself: the heading and what follows it, up to the next heading
    for h in list(tree.iter(*HEADING_TAGS)):
        if h.getparent() is None or not is_policy_title(h.text_content()):
            continue
        level, sib = h.tag, h.getnext()
        while sib is not None and not (isinstance(sib.tag, str) and sib.tag in HEADING_TAGS and sib.tag <= level):
            nxt = sib.getnext()
            _drop(sib)
            sib = nxt
        _drop(h)


def news_lines(tree) -> list[str]:
    """The items of a karnataka.gov.in news list, one line each, without list numbers or ages; [] when the
    page has no such list."""
    blocks = tree.xpath(NEWS_BLOCK_XPATH)
    if not blocks:
        return []
    lines = []
    for block in blocks + tree.xpath(NEWS_EXTRA_XPATH):
        for el in block.iter(*NEWS_ITEM_TAGS):
            if next(el.iterdescendants(*NEWS_ITEM_TAGS), None) is not None:
                continue  # the innermost element holds the item
            line = AGE_TAIL_RE.sub("", ITEM_NUMBER_RE.sub("", WS_RE.sub(" ", el.text_content()).strip()))
            if line:
                lines.append(line)
    return lines


def _pruned(page_html: str):
    tree = load_html(page_html)
    if tree is None:
        return page_html
    strip_template_blocks(tree)
    return tree


def page_lines(page_html: str) -> list[str]:
    """The page's main text as comparable lines, without stamp lines, policy blocks or JavaScript notices."""
    tree = _pruned(page_html)
    news = news_lines(tree) if not isinstance(tree, str) else []
    text = "\n".join(news) or trafilatura.extract(tree, include_tables=True, include_comments=False, favor_recall=True)
    if not text or all(is_js_notice(WS_RE.sub(" ", line).strip()) for line in text.splitlines() if line.strip()):
        # pages that are mostly links and menus: fall back to all visible text, split into sentences
        visible = trafilatura.html2txt(
            lxml_html.tostring(tree, encoding="unicode") if not isinstance(tree, str) else tree
        )
        text = "\n".join(SENTENCE_SPLIT_RE.split(visible or ""))
    lines = [WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    kept, after_stamp = [], False
    for line in lines:
        if len(line) < MIN_LINE_CHARS or is_js_notice(line):
            continue
        if is_stamp_line(line) or (after_stamp and _is_bare_date_time(line)):
            after_stamp = True
            continue
        after_stamp = False
        kept.append(line)
    return kept


def _js_only(old: list[str]) -> bool:
    return bool(old) and all(is_js_notice(line) for line in old)


def looks_blind(previous_text: str) -> bool:
    """True when a stored snapshot may never have seen the page: it is only JavaScript notices, or it
    carries policy wording. Such a snapshot is fetched in full, never with a conditional GET."""
    old = [line for line in previous_text.splitlines() if line.strip()]
    return _js_only(old) or any(is_policy_title(line) or POLICY_MENTION_RE.search(line) for line in old)


def was_blind(previous_text: str, new_lines: list[str]) -> bool:
    """True when the stored snapshot never saw the page (taken before the policy and JavaScript rules):
    it is only JavaScript notices, or it carries policy wording and shares no line with the new text."""
    old = [line for line in previous_text.splitlines() if line.strip()]
    return looks_blind(previous_text) and (_js_only(old) or not set(old) & set(new_lines))


def snapshot_of(lines: list[str], title: str) -> PageSnapshot:
    text = "\n".join(lines)
    # hash the set of lines, so content that only moved around is not a change
    digest = hashlib.sha256("\n".join(sorted(set(lines))).encode("utf-8")).hexdigest()
    return PageSnapshot(digest, text, title)


def change_summary(old_text: str, new_text: str) -> str:
    old_set, new_set = set(old_text.splitlines()), set(new_text.splitlines())
    added = [line for line in new_text.splitlines() if line not in old_set]
    removed = [line for line in old_text.splitlines() if line not in new_set]
    parts = []
    if added:
        parts.append("Added: " + " | ".join(added))
    if removed:
        parts.append("Removed: " + " | ".join(removed))
    summary = " || ".join(parts)
    return summary if len(summary) <= SUMMARY_CHARS else summary[: SUMMARY_CHARS - 1] + "…"


def _read_page(content: bytes) -> tuple[str, list[str]]:
    """Decode and extract (slow, CPU-bound: run in a worker thread)."""
    page_html = decode_file(content[:MAX_EXTRACT_BYTES])
    return page_html, page_lines(page_html)


async def monitor_page(
    client: PoliteClient,
    source: Source,
    url: str,
    *,
    previous: PageSnapshot | None = None,
    validators: Validators | None = None,
    now: datetime | None = None,
) -> FetchOutcome:
    out = FetchOutcome(source.source_id, source.route_type, "error", url=url, snapshot=previous)
    started = time.monotonic()
    # no snapshot yet, or a blind one: ask for the full page, or a 304 would keep it that way forever
    v = (validators or Validators()) if previous is not None and not looks_blind(previous.text) else Validators()
    try:
        res = await client.fetch(url, accept=PAGE_ACCEPT, etag=v.etag, last_modified=v.last_modified)
        out.http_status, out.error_kind = res.status, res.error_kind
        if res.not_modified:
            out.status, out.validators = "not_modified", v
        elif not res.ok:
            out.reason = res.error or f"HTTP {res.status}"
        elif not looks_like_html(res.content, res.content_type):
            out.reason = f"not a web page ({res.content_type or 'unknown type'})"
        else:
            page_html, lines = await asyncio.wait_for(asyncio.to_thread(_read_page, res.content), EXTRACT_TIMEOUT)
            title = page_title(page_html)
            if is_bot_check(title, page_html):
                out.reason = f"blocked by a bot check ({title or 'no title'}); kept the last snapshot"
            elif not lines:
                out.reason = NEEDS_JS_REASON
            else:
                snap = snapshot_of(lines, title)
                out.status, out.snapshot, out.feed_title = "ok", snap, title
                out.validators = Validators(res.etag, res.last_modified)
                blind_before = previous is not None and was_blind(previous.text, lines)
                if previous is not None and snap.text_hash != previous.text_hash and not blind_before:
                    out.entries = [
                        Entry(
                            title=f"{title or source.name} updated",
                            link=url,
                            published=now or datetime.now(timezone.utc),
                            summary=change_summary(previous.text, snap.text),
                            # both hashes, so a page flipping A->B->A->B gives each change its own id
                            guid=f"{source.source_id}:{previous.text_hash[:12]}-{snap.text_hash[:12]}",
                        )
                    ]
    except TimeoutError:
        out.status, out.snapshot, out.entries = "error", previous, []
        out.reason = f"text extraction took over {EXTRACT_TIMEOUT:.0f}s; kept the last snapshot"
    except Exception as exc:  # extraction trouble on one page must not lose its snapshot or stop the run
        out.status, out.snapshot, out.entries = "error", previous, []
        out.reason = f"{type(exc).__name__}: {exc}"[:300]
    out.elapsed = round(time.monotonic() - started, 2)
    return out
