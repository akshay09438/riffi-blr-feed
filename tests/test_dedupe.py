import time
from datetime import datetime, timedelta, timezone

from riffi_ingest.dedupe import Clusterer, similarity
from riffi_ingest.normalise import Item, item_id, norm_title

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)


def item(title, url=None, source_id="S011", publisher="Deccan Herald", hours_ago=1):
    url = url or "https://a.in/" + title.lower().replace(" ", "-")[:60]
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


def test_same_url_is_the_same_item():
    c = Clusterer()
    first = c.add(item("Tunnel road tender floated", url="https://dh.com/1"))
    again = c.add(item("Tunnel road tender floated (updated)", url="https://dh.com/1", source_id="S019"))
    assert first.added and first.new_cluster
    assert not again.added and again.cluster is first.cluster and len(first.cluster.members) == 1


def test_the_same_story_from_several_outlets_is_one_cluster():
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
    assert len(c.clusters) == 2


def test_a_short_title_does_not_swallow_other_stories():
    c = Clusterer()
    c.add(item("Bengaluru rains: schools shut tomorrow", hours_ago=2))
    short = c.add(item("Bengaluru", hours_ago=1))
    assert short.new_cluster
    assert similarity(norm_title("Bengaluru"), norm_title("Bengaluru rains schools shut")) == 0.0
    assert similarity(norm_title("Metro fare hike"), norm_title("Namma Metro fare hike announced")) == 100.0


def test_near_identical_short_titles_still_match():
    c = Clusterer()
    a = c.add(item("Metro fare hike", url="https://a.in/1"))
    b = c.add(item("Metro fare hike!", url="https://b.in/1", source_id="S019"))
    assert b.cluster is a.cluster


def test_stories_more_than_48_hours_apart_stay_separate():
    c = Clusterer()
    old = c.add(item("Cauvery water dispute hearing in Supreme Court", hours_ago=60))
    new = c.add(item("Cauvery water dispute hearing in Supreme Court", url="https://b.in/x", hours_ago=1))
    assert new.new_cluster and new.cluster is not old.cluster


def test_page_monitor_updates_never_merge_by_title():
    c = Clusterer()
    a = c.add(
        item("Home updated", url="https://bwssb.karnataka.gov.in/", source_id="S110"), route_type="Web page monitor"
    )
    b = c.add(
        item("Home updated", url="https://mybmtc.karnataka.gov.in/", source_id="S109"), route_type="Web page monitor"
    )
    d = c.add(item("Home updated", url="https://news.in/home-updated", source_id="S011"))
    assert a.new_cluster and b.new_cluster and d.new_cluster


def test_kannada_titles_cluster_too():
    c = Clusterer()
    a = c.add(item("ಮೆಟ್ರೋ ದರ ಏರಿಕೆ ಇಂದಿನಿಂದ ಜಾರಿ", url="https://p.in/1"))
    b = c.add(item("ಮೆಟ್ರೋ ದರ ಏರಿಕೆ ಇಂದಿನಿಂದ", url="https://t.in/1", source_id="S102"))
    assert b.cluster is a.cluster


def test_earlier_stories_can_be_passed_back_in():
    first = Clusterer()
    story = first.add(item("Karnataka cabinet approves caste survey report")).cluster
    second = Clusterer(existing=[story])
    joined = second.add(
        item("Cabinet approves caste survey report in Karnataka", url="https://b.in/2", source_id="S019")
    )
    assert joined.cluster is story and story.source_count == 2


def test_a_days_volume_clusters_in_seconds():
    words = [f"word{i}" for i in range(400)]
    items = [
        item(" ".join(words[(i * 7 + k) % 400] for k in range(6)), url=f"https://a.in/{i}", hours_ago=(i % 40))
        for i in range(5000)
    ]
    started = time.monotonic()
    Clusterer().add_all(items)
    assert time.monotonic() - started < 10  # about 1.5 s in the cloud; was 19 s before title de-duplication
