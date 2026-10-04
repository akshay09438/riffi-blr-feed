import textwrap

import pytest

from riffi_ingest.tagging.keywords import KeywordTagger, check_against_topics_csv, compile_phrases

CONFIG = textwrap.dedent(
    """
    local_context: ["Bengaluru", "Bangalore", "Karnataka", "BBMP", "ಬೆಂಗಳೂರ"]
    topics:
      B01:
        label: Tunnel road
        keywords: ["tunnel road", "tunnel roads", "B-SMILE", "Hebbal", "ಸುರಂಗ ರಸ್ತೆ"]
      B02:
        label: Bus and auto fares
        keywords: ["fare", "fares", "fare hike"]
        local: true
      D01:
        label: India vs New Zealand
        keywords: ["Ind vs NZ", "India vs New Zealand"]
        exclude: ["women's"]
    """
)


@pytest.fixture
def tagger(tmp_path):
    path = tmp_path / "topic_keywords.yaml"
    path.write_text(CONFIG, encoding="utf-8")
    return KeywordTagger.load(path)


def test_keywords_match_case_insensitively_on_whole_words(tagger):
    r = tagger.tag("BBMP floats TUNNEL ROAD tender; Hebbal to Silk Board")
    assert r.topics == {"B01": ["hebbal", "tunnel road"]}
    assert tagger.tag("Tunnel roadblock near Hebbalu village").topics == {}  # no partial words


def test_whitespace_and_hyphen_variants(tagger):
    assert "B01" in tagger.tag("Tunnel\n  road plan").topics
    assert "B01" in tagger.tag("Govt pushes B-SMILE projects").topics
    assert tagger.tag("BSMILE").topics == {}  # an unlisted variant does not match


def test_kannada_matches_at_word_start_with_suffixes(tagger):
    assert "B01" in tagger.tag("ಸುರಂಗ ರಸ್ತೆಗೆ ಟೆಂಡರ್").topics  # stem + suffix
    assert tagger.tag("ಬೆಂಗಳೂರಿನಲ್ಲಿ ಮಳೆ").local
    assert not tagger.tag("ಹೊಸಸುರಂಗ ರಸ್ತೆ").topics  # not at a word start


def test_local_topics_need_a_place(tagger):
    assert tagger.tag("Delhi metro fare hike").topics == {}
    assert tagger.tag("BMTC bus fare hike in Bengaluru").topics == {"B02": ["fare hike"]}
    assert tagger.tag("welfare scheme in Karnataka").topics == {}  # "fare" is not inside "welfare"


def test_exclude_vetoes(tagger):
    assert "D01" in tagger.tag("Ind vs NZ: India win the toss").topics
    assert "D01" not in tagger.tag("Ind vs NZ women's T20I").topics


def test_unmatched_local(tagger):
    assert tagger.tag("Bengaluru rains lash the city").unmatched_local
    assert not tagger.tag("Mumbai rains lash the city").unmatched_local
    assert not tagger.tag("Bengaluru tunnel road row").unmatched_local


def test_a_story_is_tagged_from_all_its_reports(tagger):
    r = tagger.tag_texts(["Bus fare hike from Monday", "BMTC revises fares in Bengaluru", None])
    assert r.topics == {"B02": ["fare hike", "fares"]} and r.local


def test_regex_characters_in_keywords_are_plain_text():
    p = compile_phrases(["C++", "Rs 1.5 lakh", "(PUC)"])
    assert p.search("Learn C++ today") and p.search("costs Rs 1.5 lakh") and p.search("results (PUC) out")
    assert not p.search("Rs 105 lakh")


# ---- the real keyword file


@pytest.fixture(scope="module")
def real(repo_root_module):
    return KeywordTagger.load(repo_root_module / "config" / "topic_keywords.yaml")


@pytest.fixture(scope="module")
def repo_root_module():
    from tests.conftest import REPO_ROOT

    return REPO_ROOT


def test_every_topic_in_topics_csv_has_5_to_15_keywords(real, repo_root_module):
    assert check_against_topics_csv(real, repo_root_module / "topics.csv") == []
    assert len(real.topics) == 151
    for t in real.topics:
        assert 5 <= len(t.keywords) <= 15, t.topic_id
        assert t.label and len(t.label) <= 60, t.topic_id


@pytest.mark.parametrize(
    "headline,topic",
    [
        ("BBMP floats tender for Hebbal–Silk Board tunnel road", "O06"),
        ("Namma Metro Yellow Line to open on October 15, says BMRCL", "B04"),
        ("Karnataka cabinet approves caste survey report", "K04"),
        ("Infosys asks employees to return to office three days a week", "B16"),
        ("Water tariff hike: BWSSB to raise rates from November", "O12"),
        ("ಬೆಂಗಳೂರಿನಲ್ಲಿ ಭಾರಿ ಮಳೆ, ಶಾಲೆಗಳಿಗೆ ರಜೆ", "O11"),
    ],
)
def test_real_keywords_catch_typical_headlines(real, headline, topic):
    assert topic in real.tag(headline).topics


def test_real_keywords_leave_unrelated_news_alone(real):
    for headline in (
        "Gold rate today in Mumbai",
        "Tenant eviction rules tightened in Delhi",
        "Stock markets close flat",
    ):
        assert real.tag(headline).topics == {}, headline
