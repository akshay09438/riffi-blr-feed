import time
from datetime import datetime, timedelta, timezone

import pytest

from riffi_ingest.dedupe import Cluster, Clusterer, Member, similarity
from riffi_ingest.normalise import Item, item_id, norm_title

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)

# Labelled Bengaluru headline pairs. The matching rules were chosen on these: every SAME pair must merge
# and no DIFFERENT pair may. Add a pair here whenever a live run shows a wrong merge or a missed one.
SAME = [
    ("BBMP floats tender for tunnel road", "Tunnel road tender floated by BBMP"),
    ("Tunnel road: BBMP floats tender", "BBMP floats tender for tunnel road"),
    ("Bengaluru rains: schools shut tomorrow", "Schools shut tomorrow as rains lash Bengaluru"),
    ("Metro fare hike", "Metro fare hike!"),
    ("Karnataka cabinet approves caste survey report", "Cabinet approves caste survey report in Karnataka"),
    ("ಮೆಟ್ರೋ ದರ ಏರಿಕೆ ಇಂದಿನಿಂದ ಜಾರಿ", "ಮೆಟ್ರೋ ದರ ಏರಿಕೆ ಇಂದಿನಿಂದ"),
    ("Siddaramaiah to meet Modi in Delhi today", "CM Siddaramaiah to meet PM Modi in Delhi today"),
    ("Namma Metro Yellow Line to open on October 15", "Yellow Line of Namma Metro to open on October 15"),
    ("5 killed in road accident on Mysuru highway", "Five killed in road accident on Mysuru highway"),
    ("BMTC bus fares hiked by 15%", "BMTC hikes bus fares by 15%"),
    ("Bigg Boss Kannada 12: contestant list revealed", "Bigg Boss Kannada 12 contestants list revealed"),
    # live run, 5 Oct 2026: one story in three clusters (S024, S065); the other two pairs are in KNOWN_SPLITS
    ("Namma Metro Timings Extended on October 3 in Bengaluru", "Namma Metro extends last-train timings on 3 October"),
    ("Power cut in Bangalore on Oct 3rd", "Power cut in Bengaluru on 3 October"),
]
# Same story, but in different words: title matching cannot join these without also joining
# "Namma Metro fare hike from October 3" (a DIFFERENT pair below). Left to the AI pass; strict, so a
# fix that joins them shows up here and the pair moves to SAME.
KNOWN_SPLITS = [
    (
        "Namma Metro services to run beyond midnight on October 3",
        "Namma Metro Timings Extended on October 3 in Bengaluru",
    ),
    ("Namma Metro services to run beyond midnight on October 3", "Namma Metro extends last-train timings on 3 October"),
]
DIFFERENT = [
    ("Metro fare hike", "Metro fare hike rolled back"),
    ("Namma Metro Yellow Line", "Namma Metro Yellow Line to open on Oct 15"),
    ("Namma Metro Yellow Line to open on Oct 15", "Namma Metro Yellow Line trial run suspended after glitch"),
    ("Bengaluru traffic police fine drunk drivers", "Bengaluru traffic police issue advisory for Dasara"),
    ("5 killed in road accident on Mysuru highway", "12 killed in road accident on Mysuru highway"),
    ("Petrol price in Bengaluru today October 3", "Petrol price in Bengaluru today October 4"),
    ("SSLC result 2026 declared", "PUC result 2026 declared"),
    ("BJP wins Bihar assembly election", "Congress wins Bihar assembly election"),
    ("RCB vs CSK live score", "RCB vs MI live score"),
    ("Power cut in Bengaluru today", "Power cut in Bengaluru tomorrow"),
    ("Metro fare hike in Delhi", "Metro fare hike in Bengaluru"),
    ("ಬೆಂಗಳೂರು", "ಬೆಂಗಳೂರಿನಲ್ಲಿ ಭಾರಿ ಮಳೆ, ಶಾಲೆಗಳಿಗೆ ರಜೆ"),
    ("Bengaluru", "Bengaluru rains schools shut"),
    # added 5 Oct 2026 with date matching, word stemming and the Namma Metro = Bengaluru rule
    ("Namma Metro timings extended on October 3", "Namma Metro timings extended on October 10"),
    ("Power cut in Bengaluru on October 3", "Power cut in Bengaluru on 4 October"),
    ("Namma Metro Yellow Line fare hike", "Namma Metro Purple Line timings extended"),
    ("Namma Metro Purple Line timings extended on Oct 3", "Namma Metro Yellow Line timings extended on Oct 3"),
    ("Metro fare hike in Delhi", "Namma Metro fare hike in Bengaluru"),
    ("Namma Metro fare hike from October 3", "Namma Metro services to run beyond midnight on October 3"),
]


def item(title, url=None, source_id="S011", publisher="Deccan Herald", hours_ago=1.0):
    url = url or "https://a.in/" + str(abs(hash((title, source_id, hours_ago))))
    return Item(
        item_id=item_id(url),
        source_id=source_id,
        title=title,
        url=url,
        canonical_url=url,
        publisher=publisher,
        published_at=NOW - timedelta(hours=hours_ago),
        fetched_at=NOW,
    )


@pytest.mark.parametrize("a,b", SAME)
def test_same_story_pairs_merge(a, b):
    c = Clusterer()
    first = c.add(item(a, source_id="S011", hours_ago=2))
    second = c.add(item(b, source_id="S019", hours_ago=1))
    assert second.cluster is first.cluster, (a, b)


@pytest.mark.xfail(strict=True, reason="same story in different words; needs meaning, not title matching")
@pytest.mark.parametrize("a,b", KNOWN_SPLITS)
def test_known_splits_still_split(a, b):
    test_same_story_pairs_merge(a, b)


@pytest.mark.parametrize("a,b", DIFFERENT)
def test_different_story_pairs_stay_apart(a, b):
    c = Clusterer()
    c.add(item(a, source_id="S011", hours_ago=2))
    second = c.add(item(b, source_id="S019", hours_ago=1))
    assert second.new_cluster, (a, b)
    assert similarity(norm_title(a), norm_title(b)) == 0.0


def test_same_url_is_the_same_item():
    c = Clusterer()
    first = c.add(item("Tunnel road tender floated", url="https://dh.com/1"))
    again = c.add(item("Tunnel road tender floated (updated)", url="https://dh.com/1", source_id="S019"))
    assert first.added and first.new_cluster
    assert not again.added and again.cluster is first.cluster and len(first.cluster.members) == 1


def test_a_story_from_several_outlets_counts_its_sources():
    c = Clusterer()
    results = c.add_all(
        [
            item("BBMP floats tender for tunnel road", source_id="S011", publisher="Deccan Herald", hours_ago=3),
            item("Tunnel road tender floated by BBMP", source_id="S019", publisher="The Hindu", hours_ago=2),
            item("Tunnel road: BBMP floats tender", source_id="S020", publisher="TOI", hours_ago=1),
            item("Metro fare hike from Monday", source_id="S011", hours_ago=1),
        ]
    )
    tunnel = results[0].cluster
    assert [r.cluster is tunnel for r in results] == [True, True, True, False]
    assert tunnel.headline == "BBMP floats tender for tunnel road"  # the earliest report
    assert tunnel.source_count == 3 and tunnel.publisher_count == 3
    assert tunnel.sources == ["S011", "S019", "S020"]


def test_a_publisher_suffix_does_not_hide_a_match():
    c = Clusterer()
    a = c.add(item("Schools shut as rains lash Bengaluru – The Hindu", publisher="The Hindu", hours_ago=2))
    b = c.add(item("Bengaluru rains: schools shut | DH", publisher="DH", source_id="S019"))
    assert b.cluster is a.cluster


def test_the_window_counts_from_the_first_report_so_daily_items_do_not_chain():
    c = Clusterer()
    for day in range(10):
        c.add(item("Gold rate today in Bengaluru", hours_ago=24 * (10 - day)))
    assert len(c.clusters) >= 4  # a new story at least every 48 h, never one story spanning ten days
    for story in c.clusters.values():
        times = [m.published_at for m in story.members]
        assert max(times) - min(times) <= timedelta(hours=48)


def test_page_monitor_updates_never_merge_by_title():
    c = Clusterer()
    a = c.add(item("Home updated", source_id="S110"), route_type="Web page monitor")
    b = c.add(item("Home updated", source_id="S109"), route_type="Web page monitor")
    d = c.add(item("Home updated", source_id="S011"))
    assert a.new_cluster and b.new_cluster and d.new_cluster


def test_kannada_words_stay_whole():
    assert norm_title("ಬೆಂಗಳೂರು: ಮಳೆ!") == "ಬೆಂಗಳೂರು ಮಳೆ"


def test_earlier_stories_can_be_passed_back_in():
    story = Clusterer().add(item("Karnataka cabinet approves caste survey report", hours_ago=5)).cluster
    second = Clusterer(existing=[story])
    joined = second.add(item("Cabinet approves caste survey report in Karnataka", source_id="S019"))
    assert joined.cluster is story and story.source_count == 2


def test_odd_items_do_not_crash():
    c = Clusterer()
    none_title = item("x")
    none_title.title = None
    assert c.add(none_title).new_cluster
    aware = c.add(item("Cauvery water released to Tamil Nadu today", source_id="S019", hours_ago=3))
    naive = item("Cauvery water released to Tamil Nadu today")
    naive.published_at = datetime(2026, 10, 4, 5, 0)  # no timezone, as SQLite may hand it back
    assert c.add(naive).cluster is aware.cluster


def test_a_days_volume_clusters_in_seconds():
    words = [f"word{i}" for i in range(400)]
    items = [
        item(" ".join(words[(i * 7 + k) % 400] for k in range(6)), url=f"https://a.in/{i}", hours_ago=(i % 40))
        for i in range(5000)
    ]
    started = time.monotonic()
    Clusterer().add_all(items)
    assert time.monotonic() - started < 10  # about 1.5 s in the cloud


def test_a_run_against_48_hours_of_stories_is_quick():
    # 48 h of history (about 16,000 items) as step 6 will load it, then one 30-minute run's 200 new items
    words = [f"w{i}" for i in range(3000)]
    history = []
    for i in range(16000):
        title = " ".join(words[(i * 13 + k * 101) % 3000] for k in range(7))
        when = NOW - timedelta(hours=1 + (i % 47))
        member = Member(f"h{i}", "S011", "DH", title, norm_title(title), when)
        history.append(Cluster(f"h{i}", title, when, members=[member]))
    run = Clusterer(existing=history)
    started = time.monotonic()
    for i in range(200):
        run.add(item(" ".join(words[(i * 29 + k * 7) % 3000] for k in range(7)), url=f"https://n.in/{i}"))
    assert time.monotonic() - started < 20  # about 7 s in the cloud
