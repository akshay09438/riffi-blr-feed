# Proposal 4c: the safety filter (Gate 0a word lists and `safety_state`)

*For Zuko, and the founder's explicit yes. Status: proposal only. No dangerous-list file has been changed. Part of `docs/prd/PRD_riffi_news_engine.md` Phase 4 (item c). Policy: D-017 and the founder's two-level rule, 28 Oct 2026.*

## 1. What this does, in plain words

Today nothing drops a communal or religious story automatically (D-016). The only protection is the AI pass's "excluded" answer, and a story the AI has not reached is not checked at all. This proposal adds a first check that runs on every story before the AI pass, using word lists the team can edit, and a single rule that decides whether a story may ever reach `picks.json` and the seed agent.

Every story ends up in exactly one of four states (`safety_state`):

| State | Meaning | Who sees it |
|---|---|---|
| `excluded` | Never touched, never ranked, dropped everywhere. Sticky: nothing can set it back. | Nobody (kept in the database for audit) |
| `editor_only` | Tracked and shown to the content team, never exported to the seed agent, never in `picks.json`. | Editors |
| `pending` | Nobody has finished checking it (the AI pass has not run on it). Treated as not safe. | Editors, in the audit digest only |
| `cleared` | Passed every check. The only state that can reach `picks.json`. | Editors and the seed agent |

The rule that matters most is **fail-closed**: a story the AI pass has not checked can never be `cleared`.

## 2. Policy, as the founder decided it

Two levels: *tracked (editor only)* and *eligible for picks / seed agent*.

| Subject | Result | How it is caught |
|---|---|---|
| Communal or religious flashpoints (Tipu Jayanti, conversion and cattle bills, "infiltrators", Dharmasthala, temple-fund bills, hijab and halal rows, communal violence, hate speech, accusations aimed at a community's voters, rows over a festival or place of worship, "Urdu Habba"-type rows) | `excluded`, everywhere, sticky | Word list (rules `flashpoint_*`), then the AI's `excluded` |
| Other religion stories | `editor_only` | Word list (`religion_general`) |
| Festivals as logistics only (holidays, traffic advisories, closures, sales, city events) | May be picked. Never religious content. | `religion_general` is lifted only when a logistics word is present and no religious-content word is |
| Caste survey, reservation | `editor_only`, never picks | Word list (`caste_reservation`) and topics O05, K04, P15 |
| Kannada vs Hindi, locals vs migrants | `editor_only` | Word list (`language_identity`) and topics B21, B22, SI03, O18, K03 |
| Policy-impact politics (fares, GST, rules, budgets, civic decisions) | Eligible | Nothing blocks it |
| Party-vs-party, for or against a named leader | `editor_only` | Word list (`party_vs_party`) and topics O02, O03, O04, K05, O26, O27 |
| Named private individuals | Not in picks | AI pass only (a word list cannot tell a private person from a public one). See section 8 |
| Sexual content | `excluded` for explicit terms, `safety_review` for ambiguous ones | Word list |
| Self-harm and suicide | `excluded` | Word list |
| Crime victims, abuse, domestic violence | `excluded` | Word list |
| Tragedies and deaths with casualties | `excluded` for clear phrases, `safety_review` for ambiguous ones | Word list |
| Illness stories, medical advice | `safety_review` (never `excluded` by words alone) | Word list, then the AI |
| Body shaming | `excluded` | Word list |

`safety_review` means "a word matched that is sometimes fine". The story is held back as `editor_only` until the AI pass has looked at it and said it is safe (needs the extended AI answer from proposal 4a; until then it stays `editor_only`).

## 3. Files in this proposal

| File | New or changed | Why it needs Zuko |
|---|---|---|
| `config/exclusions.yaml` | new | dangerous list |
| `riffi_ingest/safety/lexicon.py` | new | dangerous list (`safety/**`) |
| `riffi_ingest/safety/clearing.py` | new | dangerous list (`safety/**`) |
| `riffi_ingest/db/store.py` | one new function, `save_lexicon_exclusion` | dangerous list (`db/**`) |
| `riffi_ingest/pipeline.py` | a few lines after tagging | not protected |
| `riffi_ingest/cli.py` | new command `safety-scan` | not protected |
| `tests/test_safety_lexicon.py`, `tests/test_safety_clearing.py`, `tests/fixtures/safety_set.csv` | new | not protected |
| `docs/technical-spec.md`, `docs/implementation-plan.md`, `README.md` | updated | not protected |

No database migration. No change to `tests/conftest.py`, `requirements.txt`, CI, `llm_batches.py` or `ai_pass.md` (those are proposals 4a and 4b).

## 4. The word list file: `config/exclusions.yaml`

Plain phrases, never regexes (the same rule as `config/topic_keywords.yaml`). Matching reuses `tagging/keywords.compile_phrases` so English matches whole words only and Kannada matches at the start of a word. Nothing is copied.

```yaml
# config/exclusions.yaml - the safety word lists (PRD Gate 0a, D-017).
# Dangerous file: change it only with the founder's OK.
#
# A story is checked on the title and summary of every report in it.
# action: excluded      drop the story everywhere, for good
#         editor_only   keep it for the editors; never export to the seed agent
#         safety_review the word is sometimes fine; editor_only until the AI pass clears it
# Highest action wins: excluded > editor_only > safety_review.
#
# terms:        any one of these matches (plain phrases; case does not matter)
# requires_all: a list of groups; the rule matches only if EVERY group has a hit (e.g. community + votes + delete)
# veto:         if any of these also appear, the rule does not match (known false matches)
# lifts_with:   if any of these also appear, an editor_only rule is dropped (festival logistics). Never
#               lifts `excluded`, and never lifts a story that also hits a religious_content rule.
# lang:         en, kn, hi (Devanagari), hi-latn (Hindi in Roman letters). Informational; matching is by text.

version: 1                      # bump when you edit; recorded with every exclusion
enabled: true                   # false = the word lists do nothing (rollback switch). Sticky exclusions stay.

editor_only_topics:             # a story tagged with any of these is editor_only (topic ids from topics.csv)
  caste_reservation: [O05, K04, P15]
  language_identity: [B21, B22, SI03, O18, K03]
  party_leader: [O02, O03, O04, K05, O26, O27]
  # Add D-017's other leader/party topics here after the founder reviews the 30 Politics topics.

rules:
  - id: flashpoint_named
    action: excluded
    why: "named communal or religious flashpoint (BRIEF.md step 4 list)"
    terms: ["Tipu Jayanti", "Tipu Sultan Jayanti", "anti-conversion", "conversion bill", "forced conversion",
            "love jihad", "cow slaughter", "cattle slaughter", "cattle bill", "gau raksha", "infiltrator",
            "infiltrators", "ghuspaithiye", "Dharmasthala", "temple fund bill", "temple funds bill", "hundi money",
            "Muzrai bill", "hijab", "halal", "Urdu Habba", "Urdu festival row",
            "ಟಿಪ್ಪು ಜಯಂತಿ", "ಮತಾಂತರ", "ಗೋಹತ್ಯೆ", "ಧರ್ಮಸ್ಥಳ", "ಹಿಜಾಬ್", "ಹಲಾಲ್", "ಉರ್ದು ಹಬ್ಬ",
            "धर्मांतरण", "गोहत्या", "घुसपैठिए", "हिजाब", "dharmantaran", "gauhatya"]
    veto: ["halal certification of exports"]        # example of a veto; the founder decides if one is wanted

  - id: flashpoint_violence
    action: excluded
    why: "communal violence or hate speech"
    terms: ["communal clash", "communal clashes", "communal violence", "communal riot", "communal tension",
            "riot", "riots", "hate speech", "stone pelting", "stone-pelting", "lynching", "mob lynching",
            "ಕೋಮು ಗಲಭೆ", "ಕೋಮುಗಲಭೆ", "ದ್ವೇಷ ಭಾಷಣ", "ಕೋಮು ಸಂಘರ್ಷ", "दंगा", "दंगे", "सांप्रदायिक", "dange", "sampradayik"]

  - id: flashpoint_community_votes
    action: excluded
    why: "accusation aimed at a community's voters"
    requires_all:
      - ["Hindu", "Hindus", "Muslim", "Muslims", "Christian", "Christians", "Sikh", "Dalit", "minority",
         "minorities", "majority community", "ಹಿಂದೂ", "ಮುಸ್ಲಿಂ", "हिंदू", "मुस्लिम"]
      - ["votes", "vote bank", "votebank", "voters", "voter list", "ಮತ", "ಮತದಾರ", "वोट"]
      - ["delete", "deleting", "deleted", "conspir", "appease", "appeasement", "infiltrat", "bogus", "fake voters",
         "ಅಳಿಸ", "तुष्टीकरण"]

  - id: flashpoint_festival_row
    action: excluded
    why: "row over a religious festival or place of worship"
    requires_all:
      - ["festival", "habba", "jayanti", "puja", "procession", "namaz", "azaan", "azan", "temple", "mosque",
         "masjid", "church", "dargah", "idgah", "Eidgah", "ಹಬ್ಬ", "ದೇವಸ್ಥಾನ", "ಮಸೀದಿ", "ಚರ್ಚ್", "मंदिर", "मस्जिद"]
      - ["row", "controversy", "objection", "oppose", "opposes", "ban on", "banned", "protest against",
         "permission denied", "denied permission", "tension", "clash", "demolition", "encroachment", "vandal",
         "desecrat", "ಗಲಾಟೆ", "ವಿವಾದ", "ವಿರೋಧ", "विवाद", "विरोध"]

  - id: flashpoint_population_religion
    action: excluded
    why: "religion-wise population or religion-wise data debates"
    requires_all:
      - ["ಧರ್ಮವಾರು", "ಧರ್ಮದ ಆಧಾರ", "religion-wise", "religion wise", "religious demography", "धर्म के आधार"]
      - ["population", "census", "data", "ಜನಸಂಖ್ಯೆ", "ಜನಗಣತಿ", "आबादी", "जनसंख्या"]
    # TO CONFIRM: the real 6 Oct headline #103 must be pasted into tests/fixtures/safety_set.csv and match this.

  - id: religion_general
    action: editor_only
    why: "religion story (tracked, not for picks)"
    terms: ["temple", "mosque", "masjid", "church", "namaz", "azaan", "puja", "pooja", "prayer", "religious",
            "religion", "pilgrimage", "yatra", "darshan of the deity", "sermon", "fatwa", "ಧಾರ್ಮಿಕ", "ದೇವಸ್ಥಾನ",
            "ಮಸೀದಿ", "ಪೂಜೆ", "ನಮಾಜ್", "धार्मिक", "मंदिर"]
    lifts_with: ["holiday", "holidays", "traffic advisory", "traffic diversion", "diversion", "road closure",
                 "road closed", "closed for", "sale", "discount", "offer", "tickets", "timings", "bank holiday"]

  - id: religion_festival_name
    action: editor_only
    why: "festival story (picks only as logistics)"
    terms: ["Diwali", "Deepavali", "Dasara", "Dussehra", "Ganesh Chaturthi", "Ganesha", "Ugadi", "Eid", "Ramzan",
            "Ramadan", "Muharram", "Christmas", "Karaga", "Kadalekai Parishe", "Sankranti", "Navaratri", "Holi",
            "Janmashtami", "Mahashivaratri", "ಗಣೇಶ ಚತುರ್ಥಿ", "ದಸರಾ", "ಯುಗಾದಿ", "ಕರಗ", "ದೀಪಾವಳಿ"]
    lifts_with: ["holiday", "holidays", "traffic", "diversion", "advisory", "road closure", "closed", "sale",
                 "discount", "offer", "tickets", "timings", "bank holiday", "metro", "BMTC", "special trains"]

  - id: religious_content
    action: editor_only
    why: "religious content (never lifted)"
    terms: ["puja vidhi", "puja timings", "muhurat", "rituals", "darshan", "blessings", "aarti", "bhajan", "sermon",
            "prasad", "prasada", "temple entry", "idol"]

  - id: caste_reservation
    action: editor_only
    why: "caste survey or reservation (tracked, never picks)"
    terms: ["caste survey", "caste census", "caste-based census", "jati gananti", "reservation", "quota for",
            "OBC quota", "Lingayat", "Vokkaliga", "Veerashaiva", "EWS quota", "creamy layer", "Scheduled Caste",
            "Scheduled Tribe", "Dalit", "ಜಾತಿ ಗಣತಿ", "ಜಾತಿ ಸಮೀಕ್ಷೆ", "ಮೀಸಲಾತಿ", "आरक्षण", "जाति जनगणना"]
    veto: ["reservation of tickets", "ticket reservation", "table reservation", "hotel reservation", "seat reservation",
           "reservation counter"]

  - id: language_identity
    action: editor_only
    why: "Kannada vs Hindi or locals vs migrants (tracked, never picks)"
    terms: ["Kannada vs Hindi", "Hindi imposition", "three-language", "Hindi signboard", "Hindi signs", "nameboard row",
            "signboard row", "locals vs migrants", "outsiders", "non-Kannadigas", "north Indians", "bhaiyya",
            "Kannada rakshana", "KRV", "Karnataka Rakshana Vedike", "ಹಿಂದಿ ಹೇರಿಕೆ", "ಕನ್ನಡ ಫಲಕ", "ಹೊರರಾಜ್ಯ",
            "हिंदी थोपना", "dhaba language"]

  - id: party_vs_party
    action: editor_only
    why: "party-vs-party or for/against a named leader (tracked, never picks)"
    terms: ["slams", "hits out at", "lashes out", "targets", "attacks", "war of words", "blames Congress",
            "blames BJP", "Congress vs BJP", "BJP vs Congress", "JD(S) vs", "high command", "defection",
            "resort politics", "poaching", "no-confidence"]
    requires_all:
      - ["Congress", "BJP", "JD(S)", "JDS", "AAP", "TMC", "Siddaramaiah", "Shivakumar", "Vijayendra", "Ashoka",
         "Kumaraswamy", "Modi", "Rahul Gandhi", "Kharge", "Annamalai", "ಕಾಂಗ್ರೆಸ್", "ಬಿಜೆಪಿ", "ಜೆಡಿಎಸ್"]
    # (a rule with both `terms` and `requires_all` needs a hit in `terms` AND a hit in every requires_all group)

  - id: sexual_explicit
    action: excluded
    why: "sexual content"
    terms: ["porn", "pornography", "sex tape", "sex video", "nude", "nudes", "MMS leak", "MMS leaked", "leaked video",
            "sexually explicit", "obscene video", "adult film", "ಅಶ್ಲೀಲ", "अश्लील"]

  - id: sexual_ambiguous
    action: safety_review
    why: "word is sometimes fine (sex ratio, sex education)"
    terms: ["sex", "sexual", "sexuality", "intimate", "erotic", "adult content"]

  - id: self_harm
    action: excluded
    why: "self-harm or suicide"
    terms: ["suicide", "died by suicide", "dies by suicide", "ends life", "ended his life", "ended her life",
            "took his own life", "took her own life", "suicide note", "self-harm", "self harm", "hanged himself",
            "hanged herself", "ಆತ್ಮಹತ್ಯೆ", "आत्महत्या", "atmahatya"]
    veto: ["Suicide Squad", "suicide bomber", "suicide squad"]      # a film and a terrorism term: go to review instead
  - id: self_harm_veto_review
    action: safety_review
    why: "suicide-related word but a known non-self-harm phrase"
    terms: ["Suicide Squad", "suicide bomber", "suicide mission"]

  - id: crime_victim_abuse
    action: excluded
    why: "crime victims, abuse, domestic violence"
    terms: ["rape", "raped", "gang-rape", "gangrape", "molest", "molested", "molestation", "sexual assault",
            "sexually assaulted", "POCSO", "domestic violence", "dowry death", "dowry harassment", "acid attack",
            "honour killing", "honor killing", "stalking", "stalked", "child abuse", "sexual harassment",
            "ಅತ್ಯಾಚಾರ", "ಲೈಂಗಿಕ ಕಿರುಕುಳ", "ವರದಕ್ಷಿಣೆ", "बलात्कार", "छेड़छाड़", "balatkar"]
    veto: ["Rapeseed", "rapeseed", "grape", "drape"]               # whole-word matching already stops grape/drape

  - id: crime_abuse_ambiguous
    action: safety_review
    why: "word is sometimes fine (abuse of power, substance abuse)"
    terms: ["abuse", "abused", "assault", "assaulted", "murder", "murdered", "stabbed", "kidnapped", "ಕೊಲೆ"]

  - id: tragedy_clear
    action: excluded
    why: "tragedy or death with casualties"
    terms: ["death toll", "killed in", "killed after", "dead after", "dead in", "lost their lives", "lost his life",
            "lost her life", "bodies recovered", "body found", "stampede", "building collapse", "collapsed killing",
            "plane crash", "air crash", "train accident", "bus accident", "fatal accident", "drowned", "drowns",
            "landslide kills", "flood deaths", "died in", "ಅಪಘಾತ", "ಮೃತ", "ಸಾವು", "मौत", "हादसा"]

  - id: tragedy_ambiguous
    action: safety_review
    why: "death words: obituary, sport or figure of speech"
    terms: ["dies", "died", "death", "dead", "passes away", "passed away", "demise", "fatal", "killed", "murder case",
            "condolence"]

  - id: illness_medical
    action: safety_review
    why: "illness or medical advice (picks only if the AI says it is policy or public health)"
    terms: ["cancer", "tumour", "tumor", "diagnosed", "symptoms", "treatment for", "cure for", "home remedy",
            "remedies", "hospitalised", "hospitalized", "surgery", "dengue", "outbreak", "virus", "infection",
            "diabetes", "heart attack", "mental health", "depression"]

  - id: body_shaming
    action: excluded
    why: "body shaming"
    terms: ["body shaming", "body-shaming", "body shamed", "fat shaming", "fat-shaming", "fat shamed", "skinny shaming",
            "trolled for her weight", "trolled for his weight", "trolled over weight"]
```

Counts, languages and spellings above are a **starter draft for review, not a finished list**. The Kannada and Hindi entries must be checked by a native reader before the file is enabled (section 9). Terms that are too common to be "high precision" are deliberately set to `safety_review`, not `excluded`.

## 5. The code

### 5.1 `riffi_ingest/safety/lexicon.py` (new)

```python
"""The safety word lists (PRD Gate 0a, D-017): config/exclusions.yaml read into one object that says, for a
story's texts and topic ids, which rules hit and what the strictest action is. Pure and deterministic: the same
texts and the same file always give the same answer. Never raises on odd text."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..tagging.keywords import compile_phrases  # reuse: whole-word English, word-start Kannada

ACTIONS = ("excluded", "editor_only", "safety_review")  # strictest first
ZERO_WIDTH = re.compile("[​⁠﻿­]")  # not ZWNJ/ZWJ: Kannada needs those


def normalise(text: str) -> str:
    """NFKC, strip invisible characters (so 'ha​lal' still matches), collapse spaces."""
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", ZERO_WIDTH.sub("", text))


@dataclass(frozen=True)
class Hit:
    rule_id: str
    action: str
    why: str
    matched: tuple[str, ...]  # the words that matched, for the audit trail


@dataclass(frozen=True)
class SafetyCheck:
    action: str | None  # strictest action, or None
    hits: tuple[Hit, ...] = ()
    topic_ids: tuple[str, ...] = ()  # editor_only topics the story carries
    version: int = 0

    @property
    def excluded(self) -> bool:
        return self.action == "excluded"


@dataclass
class _Rule:
    id: str
    action: str
    why: str
    terms: re.Pattern | None
    groups: list[re.Pattern]
    veto: re.Pattern | None
    lifts_with: re.Pattern | None


class Lexicon:
    def __init__(self, rules, editor_only_topics: dict[str, list[str]], version: int, enabled: bool): ...

    @classmethod
    def load(cls, path: str | Path) -> "Lexicon":
        """Reads config/exclusions.yaml. Refuses (raises ValueError with the rule id) a rule with an unknown
        action, no terms and no requires_all, or an empty phrase - a typo must stop the run, not weaken it."""

    def check(self, texts: list[str], topic_ids: list[str] = ()) -> SafetyCheck:
        """texts = every report's 'title\\nsummary' (store.story_texts). A rule hits when (terms and every
        requires_all group match) and no veto matches. An editor_only rule is dropped when a lifts_with word is
        present, unless a religious_content rule also hit. Strictest action wins."""
```

### 5.2 `riffi_ingest/safety/clearing.py` (new): the one place `cleared` is decided

```python
"""safety_state: the single rule for whether a story may reach picks.json and the seed agent (fail-closed)."""

from dataclasses import dataclass

STATES = ("excluded", "editor_only", "pending", "cleared")


@dataclass(frozen=True)
class AiSafety:
    """What the AI pass said about a story. `current` = store.AI_CHECKED_SQL (the AI has seen all its reports).
    `safety_class` is None until proposal 4a adds the field ("ok" | "editor_only" | "exclude")."""

    current: bool
    excluded: bool
    safety_class: str | None = None


def safety_state(lexicon_action: str | None, ai: AiSafety | None, *, editor_only_topic: bool) -> str:
    # 1. anything excluded stays excluded, by either layer
    if lexicon_action == "excluded" or (ai is not None and ai.excluded) or (ai and ai.safety_class == "exclude"):
        return "excluded"
    # 2. tracked-only subjects: no AI answer can lift them
    if lexicon_action == "editor_only" or editor_only_topic or (ai and ai.safety_class == "editor_only"):
        return "editor_only"
    # 3. fail-closed: not checked, or checked before it grew
    if ai is None or not ai.current:
        return "pending"
    # 4. a doubtful word needs the AI to say "ok" out loud; before 4a ships it cannot, so it stays editor_only
    if lexicon_action == "safety_review":
        return "cleared" if ai.safety_class == "ok" else "editor_only"
    return "cleared"
```

Truth table (the test checks every row):

| Lexicon | Editor-only topic | AI verdict | Result |
|---|---|---|---|
| excluded | any | any | `excluded` |
| any | any | AI excluded | `excluded` |
| editor_only | any | any non-excluded | `editor_only` |
| none | yes | any non-excluded | `editor_only` |
| none | no | none yet, or grew since | `pending` |
| safety_review | no | current, not excluded, class not "ok" (or no class yet) | `editor_only` |
| safety_review | no | current, class "ok" | `cleared` |
| none | no | current, not excluded | `cleared` |

The category flag `editor_only_until_safety` (PRD section 8.5) is a separate switch in `config/categories.yaml`, applied in Phase 3, and is removed only in Phase 5. `clearing.py` does not know about it.

### 5.3 `riffi_ingest/db/store.py`: one new function, no schema change

The sticky exclusion is stored as a row in the existing `ai_verdicts` table, because everything already built on it works unchanged: `story_facts` returns `excluded=True` whatever the story's age (`AI_CHECKED_SQL`), `save_ai_verdict` can never set it back (`store.py:372`), `waiting_stories` never sends an excluded story to the AI again, and the digest, `stories` and topic counts already drop it.

```python
def save_lexicon_exclusion(conn, cluster_id: str, reason: str, rule_ids: list[str], version: int, now) -> str:
    """Record that the word lists excluded a story, in the caller's transaction. Returns "new", "upgraded"
    (an AI verdict existed and said not excluded: it is kept, only excluded/excluded_reason change) or
    "already". Never sets excluded back to 0. batch_id 'lexicon-v<version>:<rule ids>' tells it apart from an
    AI answer; batch_written_at is '' so a later AI answer is never 'older' and replaces the text fields, while
    the exclusion stays."""
    old = conn.execute("SELECT excluded FROM ai_verdicts WHERE cluster_id = ?", (cluster_id,)).fetchone()
    if old is not None and old["excluded"]:
        return "already"
    tag = f"lexicon-v{version}:" + "+".join(sorted(rule_ids))
    if old is not None:
        conn.execute(
            "UPDATE ai_verdicts SET excluded = 1, excluded_reason = ? WHERE cluster_id = ?", (reason, cluster_id)
        )
        return "upgraded"
    conn.execute(
        "INSERT INTO ai_verdicts (cluster_id, is_new_development, whats_new, debate_angle, excluded, excluded_reason,"
        " reports_seen, batch_id, batch_written_at, checked_at) VALUES (?, 0, '', '', 1, ?, ?, ?, '', ?)",
        (cluster_id, reason, report_count(conn, cluster_id), tag, utc_iso(now)),
    )
    return "new"
```

`ai-apply` already lists "stories excluded only by a reason" for a person to check. Lexicon exclusions are listed separately by `safety-scan` (below) so the two lists never mix.

### 5.4 `riffi_ingest/pipeline.py` (not protected): run it before scoring

Right after `store.save_tags(...)` and before the scoring loop:

```python
lexicon = Lexicon.load(paths.exclusions)  # Paths gets: exclusions = PROJECT_ROOT/"config"/"exclusions.yaml"
...
for cid in touched:
    texts = store.story_texts(conn, cid)
    topic_ids = store.story_topic_ids(conn, cid)  # existing item_topics query, keyword + llm
    check = lexicon.check(texts, topic_ids)
    if check.excluded:
        with conn:
            store.save_lexicon_exclusion(
                conn,
                cid,
                check.hits[0].why,
                [h.rule_id for h in check.hits if h.action == "excluded"],
                check.version,
                now,
            )
        summary.safety_excluded += 1
    score = scorer.score(store.story_facts(conn, cid))  # now sees excluded=True
    store.save_score(conn, cid, score)
```

(If `story_topic_ids` does not exist it is a three-line read of `item_topics`; it is the only other addition to `store.py`, and I will list it in the final diff.) `RunSummary.safety_excluded` is printed by `fetch` so the founder sees "N stories excluded by the word lists".

Not-excluded results (`editor_only`, `safety_review`) are not stored. They are recomputed from the stored texts every time (the lists and the texts fully decide them), and written into the day's sidecar `reports/YYYY-MM-DD/story_facts_v2.json` by the Phase 3 filter modules:

```json
{"c-1a2b3c": {"safety": {"state": "editor_only", "lexicon_action": "editor_only",
                          "rules": ["caste_reservation"], "matched": ["caste survey"],
                          "lexicon_version": 1, "ai_current": true}}}
```

When proposal 4d (the DB migration) lands, the sidecar `safety` block moves into the `story_filter` table with no change to `clearing.py`.

### 5.5 `riffi_ingest/cli.py` (not protected): `safety-scan`

`python -m riffi_ingest safety-scan [--hours 168] [--apply]`: runs the lexicon over stored stories (default last 7 days) and prints counts per rule and per action, plus a CSV (`reports/safety-scan/<stamp>.csv`: story id, headline, action, rules, matched words). **Without `--apply` it changes nothing.** With `--apply` it records the `excluded` ones through `save_lexicon_exclusion` and re-scores them. This backfills the 3,000+ stories already stored and gives the founder a list to read before anything is dropped for good. It takes the fetch lock like `ai-apply`.

## 6. How it interacts with the AI pass (nothing in 4a/4b is needed to ship this)

- The lexicon runs first. An `excluded` story never goes to `ai-batch` (the existing rule: a story already excluded is not sent again).
- The AI's `excluded` answer still works exactly as today and is still sticky.
- A story the lexicon marks `editor_only` or `safety_review` still goes to the AI (the AI can still exclude it), but cannot lift it to `cleared`.
- Proposal 4a will add the AI field `safety_class` ("ok" | "editor_only" | "exclude") and the never-list wording to `ai_pass.md`. Until then `clearing.py` gets `safety_class=None` and, being fail-closed, only `cleared` stories with no word-list hit pass.

## 7. Test plan

All tests use temporary databases, as `tests/test_ai_pass.py` does; none touch `data/engine.db`.

1. **`tests/test_safety_lexicon.py`**
   - Loads the real `config/exclusions.yaml`; a malformed rule (unknown action, empty term, no terms) raises `ValueError` naming the rule.
   - Per rule: at least two headlines that hit and one near-miss that must not (veto words, `grape`/`rape`, `reservation of tickets`, `Suicide Squad` goes to review not excluded).
   - Kannada: word-start matching with suffixes (e.g. `ಹಿಜಾಬ್` inside a longer word), and zero-width characters inside an English word do not defeat a match.
   - `requires_all`: all three groups needed ("Hindu votes" without "delete" must not hit `flashpoint_community_votes`).
   - `lifts_with`: "Karaga procession: traffic diversions on Sunday" gives no action; "Karaga puja timings" stays `editor_only`; a festival row with a logistics word still gets `excluded`.
   - Deterministic: the same input twice gives identical `SafetyCheck`.
2. **`tests/test_safety_clearing.py`**: every row of the truth table, plus: no AI verdict can ever give `cleared`; `current=False` gives `pending`; excluded wins over everything.
3. **Stickiness and the database** (in the same file, with a temp DB): `save_lexicon_exclusion` then `save_ai_verdict` with `excluded=False` leaves `excluded=1`; an older-batch answer cannot undo it; `write_batches` does not send it to the AI; `story_facts(...).excluded` is true; the score label is Drop; a story that grows after exclusion stays excluded. Running the pipeline twice records the exclusion once.
4. **`safety-scan`**: without `--apply` the database is byte-for-byte unchanged (compare table counts and a checksum); with `--apply` the excluded stories become Drop.
5. **The six-October regression** (named, one test each, must always pass):
   - "Urdu Habba" at Vidhana Soudha (digest #99, Karnataka Rakshana Vedike asks the government to drop it): `excluded`.
   - "Shivakumar moves to put lid on Urdu festival row" (#185): `excluded`.
   - "R Ashoka accuses Congress of conspiring to delete Hindu votes" (#108): `excluded`.
   - The Kannada story on a panel asking for religion-wise population data (#103): `excluded`. **I could not see this headline from here** (`reports/` is gitignored and lives on the laptop). Before the test is enabled, someone copies the exact headline from `reports/2026-10-06/digest.csv` into the fixture. Until then the test is marked `xfail(strict=True)` with this reason, so it cannot be forgotten.
6. **The safety set (PRD section 11): `tests/fixtures/safety_set.csv`**, 60 labelled headlines.
   - Columns: `id, headline, summary, truth, layer, expected_action, note`. `truth` is `unsafe` or `safe`; `layer` is `lexicon` (a word list must catch it) or `ai` (needs a human reading; checked in the Phase 5 AI dry run, not by unit test).
   - Acceptance, as the PRD says: **all 30 unsafe are not `cleared`** (for the `lexicon` layer, the lexicon alone must return `excluded` or `editor_only`; the `ai` layer must be `pending` at least, and is checked end to end in Phase 5), and **at least 27 of 30 safe return no `excluded` and no `editor_only`**.
   - How to build the full 60: the starter 20 + 20 below are written by me and are synthetic. The remaining 10 + 10 must come from real headlines in `engine.db` (read-only script, a person picks and labels them), so the set is not only the cases I happened to think of. The set grows after launch: every miss the editor reports goes in as a new row before the word lists are changed.

### Starter headlines (synthetic, for review; fill the rest from real data)

Unsafe (`layer = lexicon` unless marked):

| # | Headline | Expected |
|---|---|---|
| 1 | Row over Urdu Habba at Vidhana Soudha as Rakshana Vedike demands event be dropped | excluded |
| 2 | Shivakumar moves to put lid on Urdu festival row | excluded |
| 3 | Ashoka accuses Congress of conspiring to delete Hindu votes | excluded |
| 4 | Tipu Jayanti: BJP, Congress spar ahead of state-level celebration | excluded |
| 5 | Cabinet clears amendments to anti-conversion bill | excluded |
| 6 | Hijab ban: students barred from classes at Udupi college | excluded |
| 7 | Halal meat boycott calls grow ahead of Ugadi | excluded |
| 8 | Communal clash in Shivamogga: 12 held after procession turns violent | excluded |
| 9 | BJP demands deportation drive against infiltrators in voter list | excluded |
| 10 | Temple fund bill: opposition says government eyes hundi money | excluded |
| 11 | Dharmasthala case: SIT digs at fresh site | excluded |
| 12 | Techie dies by suicide in Whitefield flat, note blames workload | excluded |
| 13 | Woman gang-raped in moving cab, driver held | excluded |
| 14 | Five killed as bus overturns on Tumakuru road | excluded |
| 15 | Death toll in landslide rises to 40 | excluded |
| 16 | Actress's leaked MMS goes viral, police register case | excluded |
| 17 | Actress body-shamed over airport look, fans hit back | excluded |
| 18 | Newlywed found dead in Koramangala, family alleges dowry harassment | excluded |
| 19 | Caste census: Lingayat, Vokkaliga bodies reject survey numbers | editor_only |
| 20 | Nameboard row: KRV workers deface Hindi signs on MG Road | editor_only |
| 21 | Locals vs migrants: minister says outsiders are taking jobs | editor_only |
| 22 | Siddaramaiah slams Vijayendra over Congress vs BJP funding charge | editor_only |
| 23 | (`ai`) Class 12 student from Jayanagar named in cheating FIR | not cleared (private individual) |
| 24 | (`ai`) Child, 9, diagnosed with rare cancer; family appeals for funds | not cleared (illness, private person) |

Safe (must not be excluded or editor-only; the last four are deliberate traps):

| # | Headline | Expected |
|---|---|---|
| 1 | Namma Metro fares to go up 10-15% from 1 Nov, BMRCL says | none |
| 2 | Karnataka lists 2027 public holidays: 24 days, three long weekends | none (logistics) |
| 3 | Traffic advisory: MG Road closed on Saturday for marathon | none |
| 4 | Diwali sale: Myntra, Amazon cut prices up to 80% | none (logistics) |
| 5 | GST Council cuts rate on insurance premiums | none |
| 6 | RBI keeps repo rate unchanged at 5.5% | none |
| 7 | K-RERA tightens rules on rent deposits | none |
| 8 | India beat New Zealand by five wickets in second T20I | none |
| 9 | RCB retain Kohli and Patidar ahead of IPL 2027 deadline | none |
| 10 | Infosys Q2 profit rises 8%, raises guidance | none |
| 11 | Zepto files draft papers for IPO | none |
| 12 | BMTC rolls out 200 electric buses on ORR | none |
| 13 | TCS asks staff to return to office five days a week | none |
| 14 | IIM Bangalore placements: median package rises to Rs 33 lakh | none |
| 15 | CAT 2026 admit cards released | none |
| 16 | Bengaluru rents up 12% in Indiranagar, report says | none |
| 17 | Karaga procession: traffic diversions in the old city on Sunday | none (festival logistics) |
| 18 | Dhoni 'killed' the chase: why the finisher still matters | none (trap: "killed") |
| 19 | Suicide Squad sequel trailer lands in India | `safety_review`, not excluded (trap) |
| 20 | Delhi AQI turns 'severe'; GRAP-4 curbs on construction | none |

(Traps 17-19 can fail the "no editor_only" bar on purpose-built edge cases; at most 3 of 30 safe may fail, as the PRD allows.)

## 8. What the word lists cannot do, said plainly

- **Named private individuals:** a word list cannot tell a private person from a public one. This relies entirely on the AI pass (proposal 4a wording: "set `excluded` or `safety_class: exclude` when a story is mainly about a named non-public person"). Fail-closed still protects: no AI answer, no `cleared`.
- **Subtle framing** ("a 'cultural' row that is really communal") and misspellings, Hinglish and new slang will slip past words. That is what the AI layer and the editor are for. Every miss goes into the safety set.
- **Over-blocking:** a wrong `excluded` is permanent (sticky) and costs one story. The risk is accepted (the same trade the founder made for the AI: "when in doubt, exclude"), but see the release question in section 9.

## 9. What I need from Zuko and the founder

Zuko:
1. Review the diffs in section 5, especially the one function in `db/store.py` and the choice to reuse `ai_verdicts` instead of a new table (no migration; section 10 explains the undo).
2. Confirm `tagging.keywords.compile_phrases` may be imported from `safety/` (no new dependency, avoids copying the matcher).
3. Confirm the `lexicon-vN:` marker in `batch_id` is acceptable, or ask for a dedicated column once 4d lands.

Founder:
1. **Word lists.** Read and edit the starter in section 4. In particular the `excluded` lists, the Kannada and Hindi terms (a native reader must check spellings and meanings), and the `editor_only_topics` ids (O02, O03, O04, K05, O26, O27 for leaders and parties; O05, K04, P15 for caste and reservation; B21, B22, SI03, O18, K03 for language and migrants).
2. **Do you want a human "release" tool?** Because exclusions are sticky, an exclusion that turns out wrong cannot be undone from the engine today. Options: (a) none; Zuko can reverse a batch by hand if needed (section 10); (b) a `safety-release <story_id>` command that only a person runs, logs and can be turned off. I recommend (a) for launch week.
3. 20 more real headlines (10 unsafe, 10 safe) for the safety set, pasted from the 6 Oct digest or later runs, including the exact Kannada headline for regression test 5.
4. A decision on illness stories: `safety_review` (held for the AI) is proposed; say if they should be `excluded` outright.

## 10. Rollout and rollback

Rollout: (1) merge with `enabled: false`; (2) run `safety-scan` on the laptop without `--apply` and read the CSV with the founder; (3) fix the lists; (4) set `enabled: true`, run `safety-scan --apply`; (5) from then on every `fetch` applies it. Back up `data/engine.db` before step 4 (the usual backup).

Rollback:
- Switch off new decisions: set `enabled: false` in `exclusions.yaml`, or revert the PR. The word lists then do nothing; no code path depends on them.
- Undo the exclusions the lists made (only if they were wrong in bulk), by Zuko, after a backup. Stories the AI excluded are not touched because their `batch_id` does not begin with `lexicon-`:

```sql
-- list first
SELECT cluster_id, excluded_reason, batch_id FROM ai_verdicts WHERE batch_id LIKE 'lexicon-%' AND whats_new = '';
-- then, with the founder's yes
DELETE FROM ai_verdicts WHERE batch_id LIKE 'lexicon-%' AND whats_new = '';
```

  (Rows the AI later replaced have a different `batch_id` and keep their exclusion, which is the intent.) The affected stories are re-scored on the next fetch or `ai-apply`.
- No schema change, so there is nothing to migrate back.

## 11. Risks

| Risk | Mitigation |
|---|---|
| A wrong exclusion is permanent | Dry-run `safety-scan` first; high-precision words only for `excluded`; ambiguous words go to `safety_review`; rollback SQL above |
| Words missed (Kannada and Hindi spellings, typos, slang) | Fail-closed `pending`; the AI layer; every editor-reported miss becomes a test row |
| One bad word in one report poisons a whole story | Accepted: the cost is one story; it is the safer direction |
| Over-blocking hides good news (sports "killed it", a policy story that quotes a leader) | Vetoes, `lifts_with`, the 30 safe headlines, `safety_review` instead of `excluded` |
| Policy lives in a YAML file that anyone can weaken | The file is on the dangerous list; Zuko and the founder review every change |
| Speed | Patterns are compiled once per run; 3,000 stories a day is small |
| The AI never finds a story the lexicon already excluded | By design; those stories are also listed in `safety-scan`'s CSV and in `fetch`'s summary |
| `picks.json` could be built without this check | Phase 3's export function calls `safety_state` and writes only `cleared`; a test asserts that nothing else can be exported |
