"""Reddit signal, keyword topics, meaning-based clustering, shortlist scoring, YouTube API."""

import asyncio
import json
import re
import zlib
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np

from feedkit import net
from feedkit.net import PoliteClient
from feedkit.parse import iso
from fetcher.db import DB
from fetcher.reddit_signal import (
    RedditGuard,
    RedditPoller,
    Sub,
    check_sub,
    parse_posts,
    requests_per_hour,
)
from fetcher.semantic import SEMANTIC_LANGS, SemanticIndex, buzz_norm, recluster, reddit_buzz
from fetcher.stories import load_clusters, score, write_shortlist
from fetcher.topics import TopicTagger
from fetcher.youtube_api import parse_items, uploads_playlist

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ bot name tag


def test_placeholder_contact_is_never_sent():
    assert "contact pending" in net.bot_user_agent("bot@<your-riffi-domain>")
    assert net.bot_user_agent("bot@riffi.in") == "RiffiFeedBot/1.0 (+mailto:bot@riffi.in)"


# ------------------------------------------------------------------ Reddit

ATOM = """<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><author><name>/u/someone</name></author>
<content type="html">&lt;div&gt;SECRET BODY TEXT&lt;/div&gt; submitted by &lt;a href="https://www.reddit.com/user/someone"&gt;/u/someone&lt;/a&gt;
&lt;a href="https://www.thehindu.com/news/cities/bangalore/metro-fares.ece?utm_source=reddit"&gt;[link]&lt;/a&gt;
&lt;a href="https://www.reddit.com/r/bangalore/comments/abc123/metro_fares/"&gt;[comments]&lt;/a&gt;</content>
<link href="https://www.reddit.com/r/bangalore/comments/abc123/metro_fares/" />
<updated>{t}</updated><title>Metro fares going up again</title></entry>
<entry><content type="html">&lt;a href="https://i.redd.it/x.jpg"&gt;[link]&lt;/a&gt;</content>
<link href="https://www.reddit.com/r/bangalore/comments/def456/traffic_pic/" />
<updated>{t}</updated><title>Silk Board at 9am</title></entry>
</feed>"""


def _sub(name="bangalore", tier=1, verdict="ACTIVE"):
    s = Sub(name, tier, "Bangalore & Karnataka", "n/a")
    s.check = {"verdict": verdict}
    return s


def test_reddit_posts_keep_only_title_link_time_and_outbound():
    posts = parse_posts(ATOM.format(t=iso(NOW)).encode(), _sub(), "top")
    first = posts[0]
    assert first["title"] == "Metro fares going up again"
    assert first["outbound_canonical"] == "https://www.thehindu.com/news/cities/bangalore/metro-fares.ece"
    assert first["top_rank"] == 1 and first["source_type"] == "reddit_signal"
    stored = json.dumps(posts)
    assert "SECRET BODY" not in stored and "someone" not in stored  # no body text, no usernames
    assert posts[1]["outbound_url"] == ""  # a reddit-hosted image is not a news link


def test_requests_per_hour_matches_the_tiers():
    subs = [_sub("a", 1), _sub("b", 2), _sub("c", 3), _sub("d", 3, verdict="PRIVATE")]
    # tier 1: 60/20 + 60/120 = 3.5; tier 2: 60/45 + 60/240 = 1.583; tier 3: 60/360 = 0.167; skipped sub adds nothing
    assert requests_per_hour(subs) == 5.2


def _reddit_client(handler):
    return PoliteClient(transport=httpx.MockTransport(handler), retries=0)


def test_429_pauses_all_reddit_and_backs_off_exponentially(tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={"x-ratelimit-remaining": "0"})

    db = DB(tmp_path / "r.db")
    subs = [_sub("a"), _sub("b"), _sub("c")]

    async def go():
        async with _reddit_client(handler) as client:
            poller = RedditPoller(db, client, subs, log_path=tmp_path / "r.log")
            first = await poller.poll_due()
            second = await poller.poll_due()  # still paused: no request at all
            return poller, first, second

    poller, first, second = _run(go())
    assert first["blocked"] and first["requests"] == 1 and len(calls) == 1  # stopped after the first 429
    assert second["paused"] and len(calls) == 1
    until = poller.guard.paused_until()
    assert timedelta(minutes=14) < until - datetime.now(timezone.utc) <= timedelta(minutes=15)
    later = poller.guard.block(429, "x")  # a second block doubles the wait
    assert timedelta(minutes=29) < later - datetime.now(timezone.utc) <= timedelta(minutes=30)
    assert "BLOCKED 429" in (tmp_path / "r.log").read_text(encoding="utf-8")


def test_poller_sends_conditional_headers_and_skips_unchanged_bodies(tmp_path):
    body = ATOM.format(t=iso(NOW)).encode()
    seen = []

    def handler(request):
        seen.append(dict(request.headers))
        return httpx.Response(200, content=body, headers={"etag": '"r1"'})

    db = DB(tmp_path / "c.db")

    async def go():
        async with _reddit_client(handler) as client:
            poller = RedditPoller(db, client, [_sub("a", 3)], log_path=tmp_path / "c.log")
            first = await poller.poll_due()
            with db.conn:
                db.conn.execute("UPDATE reddit_feeds SET next_poll_utc = '2000-01-01T00:00:00Z'")
            second = await poller.poll_due()
            return first, second

    first, second = _run(go())
    assert first["new_posts"] == 2 and second["unchanged"] == 1
    assert seen[1].get("if-none-match") == '"r1"'
    assert "reddit" in seen[0]["user-agent"].lower() or "RiffiFeedBot" in seen[0]["user-agent"]


def test_sub_check_verdicts(tmp_path):
    responses = {
        "/r/gone/new/.rss": httpx.Response(404),
        "/r/secret/new/.rss": httpx.Response(403, text="This community is private"),
        "/r/old/new/.rss": httpx.Response(200, content=ATOM.format(t=iso(NOW - timedelta(days=30))).encode()),
        "/r/live/new/.rss": httpx.Response(200, content=ATOM.format(t=iso(NOW)).encode()),
        "/r/busy/new/.rss": httpx.Response(429),
    }

    def handler(request):
        return responses[request.url.path]

    guards = {}

    async def go():
        out = {}
        async with _reddit_client(handler) as client:
            for n in ("gone", "secret", "old", "live", "busy"):
                # a fresh database per sub: since 29 Sep 2026 every 403 pauses all of Reddit
                guards[n] = RedditGuard(DB(":memory:"), tmp_path / "reddit.log")  # never the real log
                out[n] = (await check_sub(client, _sub(n), guards[n]))["verdict"]
        return out

    assert _run(go()) == {
        "gone": "BANNED_OR_MISSING",
        "secret": "PRIVATE",
        "old": "DEAD",
        "live": "ACTIVE",
        "busy": "RATE_LIMITED",
    }
    # the verdict still names a private community, but the 403 paused Reddit all the same: a block
    # page cannot be told from a private one reliably, so every 403 stops us
    assert guards["secret"].is_paused() and guards["busy"].is_paused()
    assert not guards["live"].is_paused() and not guards["gone"].is_paused()


def test_advice_posts_never_attach_by_meaning():
    from fetcher.semantic import PERSONAL_POST

    for title in (
        "My portfolio / Need some guidance",
        "How many Indian startups actually get funding?",
        "Stock Market Investing for Beginners : What I have learnt so far",
    ):
        assert PERSONAL_POST.search(title), title
    for title in (
        "Delhi Minister Parvesh Verma slaps man filming inspection",
        "CJP Leader Ashutosh Ranka Detained In Guwahati Ahead Of Assam protest",
    ):
        assert not PERSONAL_POST.search(title), title


def test_reddit_buzz_rewards_tier_and_top_rank():
    tiers = {"bangalore": 1, "twentiesindia": 3}
    top = reddit_buzz([{"subreddit": "bangalore", "top_rank": 1, "score": None}], tiers)
    low = reddit_buzz([{"subreddit": "TwentiesIndia", "top_rank": None, "score": None}], tiers)
    assert top > low > 0 and 0 < buzz_norm(top) < 1


# ------------------------------------------------------------------ keyword topics


def test_topics_local_rule_and_kannada_word_boundaries():
    t = TopicTagger()
    assert "traffic_metro" in t.tags("Bengaluru traffic gets worse at Silk Board")
    assert "traffic_metro" not in t.tags("Hyderabad ORR accident leaves two dead")  # not local
    assert "traffic_metro" in t.tags("ORR widening approved", "Bangalore & Karnataka")  # local feed
    assert "water_flooding" not in t.tags("ಚಳ್ಳಕೆರೆ ತಾಲ್ಲೂಕಿನ ಡಿಕ್ಕಿ", "Bangalore & Karnataka")  # town name, not a lake
    assert "pubs_excise" not in t.tags("ಪಬ್ಲಿಕ್ ಟಿವಿ ವರದಿ", "Bangalore & Karnataka")
    assert "layoffs" in t.tags("Startup lays off 200 employees")
    assert {x.key for x in t.filters} == {
        "rto",
        "kannada_language",
        "pubs_excise",
        "civic_elections",
        "rent_housing",
        "layoffs",
        "visas",
        "water_flooding",
        "traffic_metro",
    }


# ------------------------------------------------------------------ clustering


def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_semantic_index_joins_by_meaning_and_keeps_kannada_title_only():
    idx = SemanticIndex(threshold=0.8, dim=3)
    a = _unit([1, 0, 0])
    idx.add(1, a, "RBI keeps repo rate unchanged", NOW)
    cid, sim, how = idx.match(_unit([0.95, 0.1, 0]), "Central bank holds key rate steady", NOW)
    assert cid == 1 and how == "semantic" and sim > 0.8
    assert idx.match(_unit([0, 1, 0]), "Unrelated", NOW)[0] is None
    assert idx.match(_unit([1, 0, 0]), "Same", NOW + timedelta(hours=49))[0] is None  # outside 48h
    # Kannada: never matched by meaning, only by near-identical headline
    assert "KN" not in SEMANTIC_LANGS
    idx.add(2, None, "ಬೆಂಗಳೂರು ಮೆಟ್ರೋ ದರ ಏರಿಕೆ", NOW, lang="KN")
    assert idx.match(None, "ಬೆಂಗಳೂರು ಮೆಟ್ರೋ ದರ ಏರಿಕೆ!", NOW, "KN")[0] == 2
    assert idx.match(None, "ಕರ್ನಾಟಕದ ಬರ ಪೀಡಿತ ಪ್ರದೇಶ", NOW, "KN")[0] is None


class FakeEmbedder:
    name = "fake"

    def encode(self, texts):
        out = []
        for t in texts:
            v = np.zeros(8, dtype=np.float32)
            for w in re.findall(r"\w+", t.lower()):
                v[zlib.crc32(w.encode()) % 8] += 1  # stable across runs, unlike hash()
            out.append(v / max(np.linalg.norm(v), 1e-9))
        return np.vstack(out)


def _insert_item(db, iid, title, source, lean_feed, published, lang="EN", area="Bangalore & Karnataka"):
    db.conn.execute(
        "INSERT OR IGNORE INTO clusters (cluster_id, title, norm_title, first_published_utc, "
        "last_published_utc, first_seen_utc) VALUES (1, 'x', 'x', ?, ?, ?)",
        (published,) * 3,
    )
    db.conn.execute(
        """INSERT INTO items (id, cluster_id, feed_id, source_name, subject_area, language, title, summary, url,
             canonical_url, published_utc, fetched_utc, source_type, topics)
           VALUES (?, 1, ?, ?, ?, ?, ?, 'teaser', ?, ?, ?, ?, 'publisher', '[]')""",
        (iid, lean_feed, source, area, lang, title, f"https://x.in/{iid}", f"https://x.in/{iid}", published, published),
    )


def test_recluster_and_shortlist_scoring(tmp_path):
    db = DB(tmp_path / "s.db")
    with db.conn:
        for fid, name, lean in (
            ("L", "Left Paper", "centre-left"),
            ("R", "Right Paper", "right-of-centre"),
            ("C", "Centre Paper", "centrist"),
        ):
            db.conn.execute(
                "INSERT INTO feeds (feed_id, url, active_url, source_name, political_lean, priority) "
                "VALUES (?, ?, ?, ?, ?, 'P0')",
                (fid, f"https://{fid}", f"https://{fid}", name, lean),
            )
        t = iso(NOW - timedelta(hours=1))
        _insert_item(db, "1", "Bengaluru metro fares rise from October", "Left Paper", "L", t)
        _insert_item(db, "2", "Bengaluru metro fares rise from October says BMRCL", "Right Paper", "R", t)
        _insert_item(db, "3", "Quarterly results of a cement company", "Centre Paper", "C", t, area="Stock Markets")
    out = recluster(db, FakeEmbedder(), threshold=0.8)
    assert out["items"] == 3 and out["clusters"] == 2
    stories = load_clusters(db, iso(NOW - timedelta(hours=24)))
    metro = next(s for s in stories if "metro" in s["title"].lower())
    assert metro["source_count"] == 2
    assert {s["political_lean"] for s in metro["sources"]} == {"centre-left", "right-of-centre"}
    t = TopicTagger()
    scored = score(metro, NOW, {x.key for x in t.filters}, local_re=t.local_re)
    assert scored["components"]["lean_spread"] == 1.0  # both sides covered it
    cement = next(s for s in stories if "cement" in s["title"].lower())
    assert score(cement, NOW, set())["score"] < scored["score"]
    assert scored["primary_area"] == "Bangalore & Karnataka"
    metro.update(scored)
    sl = {
        "slots": [{"area": "Bangalore & Karnataka", "wanted": 4, "stories": [metro], "note": "only 1"}],
        "stories": [metro],
        "reddit_conversations": [],
        "more": [],
    }
    md, js = write_shortlist(sl, tmp_path / "short", "2026-09-27")
    assert "Bengaluru metro fares" in md.read_text(encoding="utf-8")
    slot = json.loads(js.read_text(encoding="utf-8"))["slots"][0]
    assert slot["area"] == "Bangalore & Karnataka" and slot["stories"][0]["components"]["lean_spread"] == 1.0


def test_shortlist_folds_close_siblings_into_one_slot(tmp_path):
    from fetcher.embed import to_blob
    from fetcher.stories import _fold_related

    db = DB(tmp_path / "f.db")
    t = iso(NOW)
    with db.conn:
        for cid, vec in ((1, [1, 0, 0]), (2, [0.9, 0.3, 0]), (3, [0, 1, 0])):
            db.conn.execute(
                "INSERT INTO clusters (cluster_id, title, norm_title, first_published_utc, last_published_utc, "
                "first_seen_utc, centroid, n_vec) VALUES (?, 'x', 'x', ?, ?, ?, ?, 1)",
                (cid, t, t, t, to_blob(_unit(vec))),
            )
    stories = [{"cluster_id": c, "title": f"s{c}", "best_link": "", "source_count": 1} for c in (1, 2, 3)]
    kept = _fold_related(db, stories, 10)
    assert [s["cluster_id"] for s in kept] == [1, 3]  # 2 is 0.95 similar to 1: listed under it
    assert kept[0]["related"][0]["cluster_id"] == 2


# ------------------------------------------------------------------ decisions: never contact what is off


def test_health_never_contacts_switched_off_or_robots_blocked_feeds(tmp_path):
    from fetcher import config as cfg
    from fetcher.health import run_health

    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(404)

    settings = cfg.Settings(
        feeds=[
            cfg.Feed(
                id="G", name="GN", url="https://news.google.com/rss/search?q=x", subject_area="Gap fill", enabled=False
            ),
            cfg.Feed(
                id="Y",
                name="YT",
                url="https://www.youtube.com/feeds/videos.xml?channel_id=UC1",
                subject_area="X",
                enabled=False,
            ),
            cfg.Feed(id="M", name="Blocked", url="https://blocked.in/feed/", subject_area="X", robots_allowed="no"),
            cfg.Feed(id="OK", name="Fine", url="https://ok.in/feed", subject_area="X"),
        ]
    )

    async def go():
        async with PoliteClient(transport=httpx.MockTransport(handler), retries=0) as client:
            return await run_health(
                settings, DB(tmp_path / "h.db"), client, log_path=tmp_path / "h.log", verified_csv=tmp_path / "none.csv"
            )

    results = _run(go())
    assert set(hosts) == {"ok.in"} and [r["id"] for r in results] == ["OK"]


# ------------------------------------------------------------------ YouTube Data API


def test_youtube_api_parsing():
    assert uploads_playlist("UCabc") == "UUabc"
    payload = {
        "items": [
            {
                "snippet": {
                    "title": "Metro explainer",
                    "description": "Long description",
                    "publishedAt": "2026-09-26T10:00:00Z",
                    "resourceId": {"videoId": "vid1"},
                }
            }
        ]
    }
    entry = parse_items(payload)[0]
    assert entry.link == "https://www.youtube.com/watch?v=vid1" and entry.published.day == 26
