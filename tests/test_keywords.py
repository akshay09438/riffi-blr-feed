import textwrap

import pytest

from riffi_ingest.tagging.keywords import KeywordTagger, compile_phrases

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
