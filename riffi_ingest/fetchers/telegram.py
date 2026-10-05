"""Telegram channels read from their public web page, t.me/s/<channel> (founder's choice, 5 Oct 2026: rsshub.app
refused all three channels). The page lists the channel's latest posts (about 20) for anyone, no login. Telegram
is not X, Instagram or WhatsApp, so the never-scrape rule does not apply.

Each post on the page is a block like this (only the parts read here):

    <div class="tgme_widget_message ..." data-post="channel/1234">
      <div class="tgme_widget_message_reply ...">  a quoted post: its text is NOT this post's text  </div>
      <div class="tgme_widget_message_text js-message_text">The post text<br>second line</div>
      <a class="tgme_widget_message_date" href="https://t.me/channel/1234"><time datetime="2026-10-05T08:12:44+00:00">

A post becomes an entry with its link, its time and its text as the summary. The title is left empty, so
cleaning takes the first sentence, as it already did for RSSHub's Telegram items. A post with no text (a photo or
sticker alone) is skipped: there is nothing to tag. A page with no posts at all is an error, not "no news": it
means Telegram changed the page or turned off the channel's public preview.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .parse import SUMMARY_CHARS, WS_RE, Entry

PAGE_BASE = "https://t.me/s/"
BLOCK_TAGS = {"br", "p", "div", "li"}  # tags that end a line of text
VOID_TAGS = {"br", "img", "meta", "link", "input", "hr", "source", "wbr"}


def page_url(fetch_url: str) -> str:
    """https://t.me/s/<channel> for a feeds.csv Telegram URL: the channel is its last path part, whatever the
    host (rsshub.app/telegram/channel/<name>, t.me/<name>, t.me/s/<name>). Empty when there is no name."""
    name = urlsplit(fetch_url).path.rstrip("/").rsplit("/", 1)[-1]
    return PAGE_BASE + name if name and name not in ("s", "channel", "telegram") else ""


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.posts: list[dict] = []
        self._depth = 0  # open tags inside the current post
        self._text_depth = None  # depth of the post's own text block while inside it
        self._skip_depth = None  # depth of a quoted reply while inside it
        self._post: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "meta" and a.get("property") == "og:title":
            self.title = (a.get("content") or "").strip()
        if self._post is None:
            if "tgme_widget_message" in classes and a.get("data-post"):
                self._post = {"id": a["data-post"], "text": [], "link": "", "time": ""}
                self._depth = 0 if tag in VOID_TAGS else 1
            return
        if tag not in VOID_TAGS:
            self._depth += 1
        if self._skip_depth is None and "tgme_widget_message_reply" in classes:
            self._skip_depth = self._depth
        elif self._skip_depth is None and self._text_depth is None and "js-message_text" in classes:
            self._text_depth = self._depth
        elif self._text_depth is not None and tag in BLOCK_TAGS:
            self._post["text"].append("\n")
        if tag == "a" and "tgme_widget_message_date" in classes:
            self._post["link"] = a.get("href") or ""
        if tag == "time" and a.get("datetime") and not self._post["time"]:
            self._post["time"] = a["datetime"]

    def handle_endtag(self, tag):
        if self._post is None or tag in VOID_TAGS:
            return
        if self._text_depth is not None and self._depth == self._text_depth:
            self._text_depth = None
        if self._skip_depth is not None and self._depth == self._skip_depth:
            self._skip_depth = None
        self._depth -= 1
        if self._depth == 0:
            self.posts.append(self._post)
            self._post, self._text_depth, self._skip_depth = None, None, None

    def handle_data(self, data):
        if self._post is not None and self._text_depth is not None and self._skip_depth is None:
            self._post["text"].append(data)


def _when(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def parse_page(content: bytes) -> tuple[str, list[Entry], int]:
    """(channel title, entries newest first, posts found). Posts with no text are counted but give no entry."""
    page = _Page()
    page.feed(content.decode("utf-8", errors="replace"))
    page.close()
    entries = []
    for post in page.posts:
        lines = [WS_RE.sub(" ", line).strip() for line in "".join(post["text"]).split("\n")]
        text = "\n".join(line for line in lines if line)
        if not text:
            continue
        link = post["link"] or "https://t.me/" + post["id"]
        entries.append(Entry(link=link, published=_when(post["time"]), summary=text[:SUMMARY_CHARS], guid=post["id"]))
    entries.reverse()  # the page lists oldest first; feeds list newest first
    return page.title, entries, len(page.posts)
