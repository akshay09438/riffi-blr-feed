# PRD + Technical Architecture: Riffi News Engine v2 (Bengaluru-only → all-category, filtered for content)

Owner: Zyra (founder) · Engineering: Claude Code on the riffi-blr-feed machine · Reviewer for protected files: Zuko
Status: approved as D-017 (28 Oct 2026). Gate 0 amended by the founder the same day (two levels: tracked/editor only vs eligible for picks). Repo copy; the founder's original is the upload of 28 Oct

Attachments that go with this PRD: `needs.csv` (75 information needs, 18 categories), `new_sources.csv` (236 feeds live-tested 28 Oct 2026).

---

## 0. How to use this document (instructions for Claude Code)

1. Read this whole PRD, then `DECISIONS.md`, `CLAUDE.md`, `BRIEF.md`. `DECISIONS.md` wins over this PRD; if they conflict, stop and say so.
2. Work in the phases in §10, in order. Each phase ends with a **stop point**: post what you did and wait for the founder's OK before the next phase.
3. Never edit a protected file (§9.1). When a requirement needs one, write the exact diff, why, and the test that proves it, into `docs/proposals/<phase>-<name>.md`, and stop for Zuko.
4. Branch per phase, small commits, run the full test suite after each commit. Update tests that pin counts.
5. The engine collects, filters, ranks and reports. It never posts, replies or republishes. Timer stays off (D-009, D-016): runs happen only on request.

---

## 1. Summary

Today riffi-blr-feed fetches 131 Bengaluru-focused sources, clusters them into stories, tags them against 151 topics, scores them on a Bengaluru-weighted formula and writes a ranked digest of every non-Drop story.

v2 does two things:

1. **Collect wider.** Cover all 18 Riffi content categories (§4) with ~300 sources, so nothing that matters to Riffi's audience is missed.
2. **Filter hard.** Out of thousands of items a day, return only the **~25 stories a day that Riffi should make content about**: safe, relevant to a tier-1 Indian 18-28 year-old (college student, young professional, Bengalurean), and with a clear debate or hook. Each pick comes with why the user cares, the two sides, and a content idea.

The everything-digest stays available for audit, but the main output becomes the **Daily Picks** shortlist.

## 2. Why

Riffi is an opinion-first platform launching in Bengaluru in early November 2026. At launch the content team and the seed-account agent (50-100 seed accounts) must post timely, debatable content every day across all categories, and make videos and posts from the day's news. They don't need all the news. They need the right 25 stories, early, with the angle already found, and with zero risk of a communal, religious or tragedy story slipping through.

## 3. Users and jobs

| User | Job | What they need from the engine |
|---|---|---|
| Content team (founder + editors) | Decide each morning what to make videos/posts about | A short ranked list per category, why it matters, the debate, a content idea, links |
| Seed-account agent (AI) | Post opinion takes from seed accounts | Only safe, approved picks with side A / side B and a hook; never raw news |
| Editor (recall + quality) | Prove sources and the filter work | A feedback sheet to mark picks good/bad and log misses |
| Zuko (engineer) | Keep the engine safe and maintainable | Proposals for protected changes, tests, health report |

The end audience: English-speaking, tier-1 urban Indians aged 18-28, Bengaluru-first: college students (tier-1 colleges) and young professionals (~₹1L+/month), heavy X and Instagram users.

## 4. Taxonomy: 18 categories and 75 needs

The 18 categories (exact strings): Bangalore, Politics, Sports, Entertainment, Tech/AI, Corporate, Finance, Stock market, Education, College life, Relationships, Social life, General, Growth, Health & fitness, Travel, Fashion & style, Beauty & grooming.

Each category has 3-8 **information needs** (`needs.csv`, need_id like BLR3 = Metro & public transport). A need is a kind of update the team wants to hear about. Needs are the unit for source coverage and for the recall test.

Hierarchy: **Category → Need → Topic (topics.csv) → Story → Pick.**

- Topic: an existing row in `topics.csv` (e.g. O25 Bigg Boss), mapped to one category and one or more needs.
- Story: a cluster of items about one development (existing concept).
- Pick: a story that passed all gates and was selected for the day's shortlist.

## 5. Scope

**In scope**
- New decision D-017 widening scope (§10, Phase 0).
- Source expansion from `new_sources.csv` with dedupe, health checks and category tagging.
- Topic expansion so every need maps to at least one topic.
- Filter pipeline: Gate 0 safety, Gate 1 relevance, Gate 2 content-ability, then selection with quotas.
- Daily Picks outputs (Markdown, CSV, JSON) and a seed-agent-safe export.
- Editor feedback loop and filter metrics.
- Run-time and capacity work within existing politeness rules.

**Out of scope (this PRD)**
- Posting, scheduling posts, or any write to X/Instagram.
- Turning the timer on.
- The dashboard UI and live `/api/stories` (D-016 still applies: not before the safety filter).
- Multi-city design (still one audience, Bengaluru-first).
- Paid sources (RSS.app X feeds, page-monitor services). Reddit sources (banned).
- Video/post generation itself (the engine only suggests the idea).

## 6. Functional requirements

### 6.1 Collection

| ID | Requirement |
|---|---|
| C1 | Every source in `feeds.csv` has `category` set to one of the 18 (re-use the existing column; it is not read by scoring or the digest today). The current 10 values are re-mapped. Secondary categories go in `topics_hint`. |
| C2 | Add new sources from `new_sources.csv` after URL-normalised dedupe against the 131 (scheme, trailing slash, utm params; Google News compared on decoded `q=`). Report duplicates, near-duplicates (same publisher section), new. |
| C3 | New source IDs continue the existing scheme (S132+). Route types and tiers use the existing vocabulary. `notes` records need_ids and the 28 Oct test status. |
| C4 | Prefer native publisher feeds. Add a Google News row only if its need has fewer than 2 live native feeds. Cap new Google News rows at 60 in this release. |
| C5 | Each new source passes `test-feeds` before `import-sources`. Dead/stale → `known_status` inactive; try the site's `/feed` or `/rss` once and record a fix. Times of India / Economic Times / Filmfare rows were untested on 28 Oct and must be tested here. |
| C6 | A full on-demand run ("news of the last 24 hours") finishes in ≤ 35 minutes on the founder's laptop. If it can't, propose a split (native first, Google News second) rather than raising request rates. |
| C7 | Every need in `needs.csv` has ≥ 1 live source after C5. Report per-need coverage (full = 2+ live incl. a native; partial = live but Google News only; none). |

### 6.2 Topics

| ID | Requirement |
|---|---|
| T1 | Re-map the 8 topic categories to the 18 (keep the old value in a new `old_category` column if useful). |
| T2 | Map each need to ≥ 1 topic. For needs without one, add evergreen/ongoing topics in the exact existing format, with keywords in `topic_keywords.yaml` (Indian spellings, brand names, Hinglish where common). |
| T3 | New topics are `local: false` unless they are Bengaluru civic topics. |
| T4 | No topic may sit on the never-list (§6.3, Gate 0). |
| T5 | Show the proposed topic list to the founder before writing it (stop point). |

### 6.3 Filter

The filter runs after clustering and tagging. A story must pass **Gate 0 → Gate 1 → Gate 2**, then **Selection** chooses the day's picks. Every story keeps its gate results so the team can audit why it was or wasn't picked.

#### Gate 0: Safety (hard, sticky)

*Amended by D-017 (28 Oct 2026, founder): two levels. **Tracked (editor only)**: the story is kept and shown to the team, never exported. **Eligible for picks / seed agent**: it can become a pick and appear in `picks.json`. The never-list below applies to picks and `picks.json`; tracking follows the brief.*

**Excluded everywhere (sticky), score Drop, in no export:**
- communal or religious flashpoints (the brief's list: Tipu Jayanti, conversion bills, cattle bills, "infiltrators" rows, the Dharmasthala case, temple-fund bills, hijab and halal rows, and any clash between religious or caste communities: hate speech, communal violence, festival or place-of-worship rows, accusations aimed at a community's voters)

**Tracked, editor only (`safety_state = editor_only`), never picks:**
- other religion and religious-practice stories (festivals are eligible for picks **only as logistics**: holidays, traffic advisories, closures, sales, city events; never religious content)
- caste survey and reservation
- Kannada vs Hindi, locals vs migrants, veg vs non-veg (identity proxies: allowed as news, never as a pre-written side)
- party-vs-party politics and for/against a named politician or party (leader and party topics in `topics.csv` carry `editor_only`)

**Kept out of picks (excluded unless the lexicon is unsure, then `safety_review`):**
- named private individuals (D-018: a *private* individual; public figures stay eligible for picks when the debate is about their public actions or statements, never their personal life)
- sexual content
- self-harm and suicide
- crime victims, abuse, domestic violence
- tragedies and deaths (accidents, disasters with casualties)
- illness stories and medical advice
- body shaming

**Eligible for picks:** policy, governance, economy and civic decisions by government when framed by impact ("new metro fares", "GST change on X", budgets, rules), not by party; holiday and festival logistics.

Implementation (two layers):
- **0a keyword layer**, before the AI pass: a deny lexicon (terms and phrases, English + Kannada + Hindi transliterations) that marks a story `excluded` with reason. High-precision terms only; ambiguous terms set `safety_review` instead.
- **0b AI layer**, inside the AI pass: the model returns `excluded` + `excluded_reason` (already in the schema) using the never-list above; "when in doubt, exclude".
- Exclusion is sticky (existing behaviour). Excluded stories score Drop and never reach any export. `editor_only` stories keep their score and appear in `picks.md`, `picks.csv` and `candidates.csv` marked `editor_only`, but never in `picks.json`.
- Files: `safety/` and `config/exclusions.yaml` are protected → this whole gate is a **Zuko proposal** (Phase 4). Until it ships, Politics, Social life and General picks are `editor_only`.

#### Gate 1: Relevance ("would our user care?")

A relevance score R (0-100) from deterministic signals plus the AI's judgement.

Signals:

| Signal | Points | How |
|---|---|---|
| Topic priority | High 25 / Medium 15 / Low 5 | best matched topic |
| Need match | +10 | story matched a topic mapped to a need |
| Geography | Bengaluru 20 / Karnataka 15 / South India 8 / Pan-India 8 / International 4 | `config/filter.yaml` only; the existing `config/scoring.yaml` (Bengaluru = 25) is unchanged (D-017) |
| Daily-life impact (AI) | 0-20 | does it change the user's money, commute, rent, job/internship, exams, phone/apps, plans, prices they pay |
| Culture pull (AI) | 0-15 | will their circle talk about it this week: a release, match, viral moment, brand or creator drama, trend |
| Audience fit (AI) | -15 to +10 | 18-28 tier-1 urban relevance; negative for retiree, parenting-kids, school exam prep, rural-only, B2B-only |
| Routine-news penalty | -15 | routine official activity with no change for the user (meetings, visits, stock tables, court listings, condolence notes) |

Pass: R ≥ 50 (configurable `relevance.pass`).

#### Gate 2: Content-ability ("can we make something from it?")

A content score K (0-100):

| Signal | Points | How |
|---|---|---|
| Debate | 0-35 | AI returns side A and side B (≤ 12 words each); 35 if both are reasonable positions real people hold, 15 if one-sided |
| Hook | 0-20 | a surprising number, contrast, or "wait, what" fact a 30-second video can open with |
| Freshness | 0-15 | first seen in the last 24 h = 15; 24-48 h = 8; older = 0 |
| Momentum | 0-15 | number of distinct sources (3+ = 15, 2 = 8) and growth since last run |
| Visual/explainability | 0-15 | can be shown or explained simply (price chart, before/after, a clip, a quote) |

Pass: K ≥ 45 (configurable `content.pass`).

#### Selection (daily shortlist)

- Final pick score P = 0.55·R + 0.45·K (configurable).
- Target 25 picks per run (`picks.target`), from stories that passed Gates 0-2.
- **Quotas** so the list covers the day, not one big story: max 4 picks per category; Bangalore min 4 if available; min 1 for each category that has any passing story; Politics max 3.
- **Diversity:** no two picks from the same story cluster; at most 2 picks per topic.
- **Tiers:** P ≥ 75 → `High`, 60-74 → `Medium`; below 60 is not picked.
- Ties: newer first, then more sources.
- If fewer than 15 stories pass, return what passed and say why in the run summary (no padding).

### 6.4 Outputs

All written to `reports/YYYY-MM-DD/` (existing folder pattern). Each run gets a run stamp `HHMM`.

| File | Who | Content |
|---|---|---|
| `picks.md` | content team | Picks grouped by category: headline, 1-line why the user cares, side A vs side B, content idea (format + hook), Riffi topic, priority, links (3 max), first seen |
| `picks.csv` | team / sheets | one row per pick: run_id, rank, category, need_id, topic_id, story_id, headline, why_care, side_a, side_b, hook, format, R, K, P, tier, safety_state, sources, links, first_seen |
| `picks.json` | seed agent | **only** picks with `safety_state = cleared` (Gate 0 shipped and passed) and not `editor_only`; fields as CSV |
| `candidates.csv` | audit | every story that reached Gate 1 with R, K, gate results and the reason it wasn't picked |
| `digest.md` | audit | existing digest, now grouped by category with counts |
| `filter_report.md` | founder | counts per gate per category, pass rates, top exclusion reasons, run time, AI stories processed vs skipped |
| `sources_health.csv` | Zuko | existing, plus category and need_id columns |

Content idea format values: `explainer reel`, `hot-take reel`, `poll`, `carousel`, `text post`, `meme`.

CLI (new commands; keep existing ones working):
- `news-24h` → full run: fetch → filter → picks (this is what "give me the news of the last 24 hours" maps to).
- `picks --date YYYY-MM-DD` → rebuild picks from stored stories without fetching.
- `feedback-template` / `apply-feedback` → editor marking (§6.5).

### 6.5 Feedback loop

- `feedback-template` writes `reports/YYYY-MM-DD/picks_feedback.csv` with each pick and empty columns: `verdict (make|maybe|no)`, `why_not (irrelevant|unsafe|stale|no-debate|duplicate|other)`, `note`. Also a `missed` section where the editor pastes stories that should have been picked.
- `apply-feedback` stores verdicts and produces `filter_metrics.md`: precision (make+maybe)/picks, unsafe count, misses per category, and a list of signals most associated with `no`.
- Weights and thresholds live in config so they can be tuned weekly without code changes. No automatic re-weighting in this release.

### 6.6 Recall test (existing second job)

Keep the editor log keyed on topic_id; add category and need_id joins so recall can be reported per category. Matching/report remain out of scope unless already planned.

## 7. Non-functional requirements

| Area | Requirement |
|---|---|
| Safety | Zero stories on the never-list in `picks.json`, ever. Gate 0 is fail-closed: if the AI pass didn't run for a story, it can't be `cleared`. |
| Run time | `news-24h` ≤ 35 min on the founder's laptop (Windows ARM64). |
| Politeness | Keep existing per-domain pacing, retries and Google News link-lookup cap (protected). No new request concurrency per domain. |
| AI budget | AI pass runs on the founder's plan via files (`ai-batch` / `ai-apply`). Cap ≤ 400 stories/run (existing). The prefilter (§8.4) must keep candidates under the cap. |
| Determinism | Same stored stories + same config → same picks. |
| Auditability | Every story keeps gate results and reasons; every pick links back to sources. |
| Config over code | All weights, thresholds, quotas and lexicons in YAML. |
| Portability | Runs unchanged when moved from the laptop to the small server. |

## 8. Technical architecture

### 8.1 Pipeline

```
feeds.csv ──► fetchers (rss, gnews, page, telegram, youtube)        [existing, protected: http.py, gnews.py]
                 │
                 ▼
            normalise ─► blocklist (copycat sites) ─► age ≤ 7d       [existing]
                 │
                 ▼
            dedupe / cluster into stories                            [existing; Bengaluru markers stay]
                 │
                 ▼
            keyword tagging → topic_ids, category, need_ids          [existing + category/need lookup]
                 │
                 ▼
   Gate 0a  keyword safety lexicon → excluded / safety_review        [NEW, protected: safety/, exclusions.yaml → Zuko]
                 │
                 ▼
            prefilter: pre-score R0 from deterministic signals;      [NEW]
            keep top N (≤ 400) for the AI pass, all Bangalore High
                 │
                 ▼
            AI pass (ai-batch → Claude Code → ai-apply)              [existing flow; prompt + schema extension → Zuko]
            returns: topic_ids, is_new_development, whats_new,
            debate_angle, excluded, excluded_reason
            + NEW: why_care, daily_life (0-20), culture (0-15),
              audience_fit (-15..10), routine (bool), side_a, side_b,
              hook, hook_score (0-20), visual (0-15), format
                 │
                 ▼
   Gate 0b  AI exclusion (sticky) ───────────────► Drop
                 │
                 ▼
   Gate 1   relevance R  ── R < pass ────────────► not picked (kept in candidates)
                 │
                 ▼
   Gate 2   content K    ── K < pass ────────────► not picked
                 │
                 ▼
   Selection: P, quotas, diversity, tiers ─────────► picks
                 │
                 ▼
   outputs: picks.md / .csv / .json, candidates.csv, digest.md, filter_report.md, sources_health.csv
```

### 8.2 Modules

New (not protected):
- `riffi_ingest/taxonomy.py` — loads `config/categories.yaml` and `needs.csv`; maps topic → category/needs; validates that every need has a topic and a live source.
- `riffi_ingest/filter/prefilter.py` — R0 from deterministic signals; chooses stories for the AI pass.
- `riffi_ingest/filter/relevance.py` — Gate 1 (R).
- `riffi_ingest/filter/content.py` — Gate 2 (K).
- `riffi_ingest/filter/select.py` — P, quotas, diversity, tiers.
- `riffi_ingest/outputs/picks.py` — picks.md/csv/json, candidates.csv, filter_report.md.
- `riffi_ingest/feedback.py` — feedback template, apply, metrics.
- CLI wiring in `cli.py` for `news-24h`, `picks`, `feedback-template`, `apply-feedback`.

Changed (not protected):
- `feeds.csv`, `topics.csv`, `config/topic_keywords.yaml`, `config/scoring.yaml` (add International geography), `scoring.py` (geography map), `outputs/digest.py` (group by category), `outputs/health_report.py` (category/need columns), `pipeline.py` (call the new stages between tagging and scoring), `tests/test_inputs.py` (new counts).

Protected → proposals for Zuko:
- `config/prompts/ai_pass.md`: audience paragraph (Bengaluru-only today) and the extended output fields; never-list wording.
- `tagging/llm_batches.py` (`check_answer` expects exactly 7 fields): accept the extended fields, with old answers still valid.
- `safety/` + `config/exclusions.yaml`: Gate 0a lexicon and the clearing logic.
- `riffi_ingest/db/**`: tables in §8.3.
- Until the DB proposal is approved, store new per-story fields in a JSON sidecar under `reports/YYYY-MM-DD/story_facts_v2.json` keyed by story_id (read by the filter modules), so Phases 2-3 aren't blocked.

### 8.3 Data model (proposed migration, Zuko)

`story_filter` (one row per story per run):
`story_id, run_id, category, need_ids, prefilter_score, ai_processed (bool), excluded (bool), excluded_reason, safety_state (cleared|editor_only|excluded|pending), R, K, P, gate1_pass, gate2_pass, why_care, side_a, side_b, hook, format, created_at`

`picks`:
`run_id, rank, story_id, category, need_id, topic_id, tier, P, exported_to_agent (bool), created_at`

`pick_feedback`:
`run_id, story_id, verdict, why_not, note, editor, created_at`

`missed_stories`:
`date, category, need_id, headline, link, note`

Indexes on (run_id), (story_id), (category, created_at).

### 8.4 Capacity plan

- Expected volume: 131 sources ≈ 3,000-8,000 items/day; ~300 sources ≈ 8,000-18,000 items/day → after clustering and the 7-day window, ~1,500-3,000 stories per 24 h run.
- Gate 0a + prefilter cut this to ≤ 400 for the AI pass: drop stories with no topic match; keep top R0 per category with per-category caps (e.g. 30 each, Bangalore 60), always keep Bangalore High-priority matches.
- Batches: keep 20 stories per batch. Send only the topic subset for the batch's categories (smaller prompt) — part of the Zuko proposal.
- Google News: keep the 2-2.5 s per-domain pacing and the 100 link-lookup cap. Order fetches native-first so slow Google News never blocks the rest; report if run time > 35 min.

### 8.5 Config files (new, not protected)

`config/categories.yaml`
```yaml
categories:
  Bangalore:   {quota_min: 4, quota_max: 4, prefilter_cap: 60}
  Politics:    {quota_min: 0, quota_max: 3, prefilter_cap: 30, editor_only_until_safety: true}
  Social life: {quota_min: 0, quota_max: 4, prefilter_cap: 30, editor_only_until_safety: true}
  General:     {quota_min: 0, quota_max: 4, prefilter_cap: 30, editor_only_until_safety: true}
  # ... all 18, default quota_min 1 if any story passes, quota_max 4, prefilter_cap 30
needs_file: needs.csv
```

`config/filter.yaml`
```yaml
relevance: {pass: 50, priority: {High: 25, Medium: 15, Low: 5}, need_match: 10,
            geography: {Bengaluru: 20, Karnataka: 15, South India: 8, Pan-India: 8, International: 4},
            routine_penalty: -15}
content:   {pass: 45, debate_two_sided: 35, debate_one_sided: 15, fresh_24h: 15, fresh_48h: 8,
            sources_3plus: 15, sources_2: 8}
picks:     {target: 25, min_to_publish: 15, weights: {R: 0.55, K: 0.45},
            tiers: {High: 75, Medium: 60}, max_per_topic: 2}
audience:
  boost_terms: [placement, internship, stipend, hostel, PG, rent, deposit, metro, BMTC, UPI, CAT, GATE,
                fees, layoffs, appraisal, WFH, concert, IPL, RCB, OTT, sale, skincare, dating app]
  penalty_terms: [pension, retirement, senior citizens, school admission, board exam, kindergarten]
```

### 8.6 AI pass extension (proposal text for `ai_pass.md`)

Audience paragraph:
> You are filtering news for Riffi, an opinion app for English-speaking tier-1 urban Indians aged 18-28, Bengaluru-first: college students and young professionals. Pick out what they would talk about or be affected by this week.

Added output fields per story: `why_care` (≤ 20 words, second person: "Your metro fare goes up ₹10…"), `daily_life` 0-20, `culture` 0-15, `audience_fit` -15..10, `routine` bool, `side_a`/`side_b` (≤ 12 words each; empty if none), `hook` (≤ 15 words), `hook_score` 0-20, `visual` 0-15, `format` (one of §6.4).
Rules: never write sides for never-list or identity-proxy subjects; set `excluded: true` when in doubt.

### 8.7 Example pick (target quality)

```
[Bangalore · BLR3 · High · P 82]
Namma Metro fares up 10-15% from 1 Nov
Why you care: your daily metro commute gets ₹10-20 costlier; second hike in 2 years.
Side A: Fair, the metro needs money to expand.   Side B: Unfair, it pushes people back to cars.
Content idea: hot-take reel — open on "₹2,400 a year more to ride the metro".
Riffi topic: Are Namma Metro fares too high for daily commuters?
Sources: The Hindu Bengaluru, Deccan Herald, Google News (BMRCL) · first seen 06:40
```

## 9. Constraints and guardrails

### 9.1 Protected files (never edit; propose to Zuko)
Database (`riffi_ingest/db/**`), safety filter (`safety/`, `blocklist.csv`, `config/exclusions.yaml`), fetch code (`fetchers/http.py`, `gnews.py`), AI prompts (`config/prompts/**`) and tagging AI code, `/api/stories`, `requirements.txt`, CI and hooks, `tests/conftest.py`, `CLAUDE.md`.

### 9.2 Decisions
- D-002 / CLAUDE.md non-goal "Bengaluru only" → needs D-017 (Phase 0).
- D-004/D-008 Google News risk and pacing → unchanged.
- D-005 AI on the founder's plan → respected via prefilter.
- D-009/D-016 timer off, no `/api/stories` before safety → unchanged.

## 10. Delivery plan (phases, stop points, acceptance)

| Phase | Work | Stop point / acceptance |
|---|---|---|
| 0 Decision | Draft D-017 for `DECISIONS.md`: "Scope widened to 18 content categories for Riffi's launch audience (18-28 tier-1 urban Indians, Bengaluru-first). Bengaluru keeps a scoring boost. One audience, no multi-city design. Supersedes D-002 for sources and topics." Write the one-line CLAUDE.md change for Zuko. | **Stop.** Founder approves D-017. |
| 1 Sources | C1-C7. Category on all sources, dedupe, add new, test-feeds, import, coverage per need, run time. | Report: before/after by category and route type, health pass rate ≥ 90% (non-X), every need ≥ 1 live source, full run ≤ 35 min. Tests green. |
| 2 Taxonomy & topics | `categories.yaml`, `taxonomy.py`, T1-T4, topic → need mapping. | **Stop** for founder approval of new topics (T5), then add. Validation: every need has a topic and a live source. |
| 3 Filter (non-protected parts) | prefilter, Gate 1, Gate 2, selection, outputs, CLI `news-24h`/`picks`, digest by category, filter_report. Uses AI fields when present, else deterministic fallbacks (R from signals only, K from freshness/momentum; picks marked `ai_missing`). Sidecar JSON storage. | Dry run on a real 24 h fetch: picks.md has ≥ 15 picks across ≥ 10 categories; candidates.csv explains every non-pick; same input → same picks. Tests for each module. |
| 4 Proposals for Zuko | (a) `ai_pass.md` audience + fields; (b) `llm_batches.py` accept extended schema + per-category topic subsets; (c) Gate 0a lexicon + `safety_state` clearing in `safety/` + `exclusions.yaml`; (d) DB migration §8.3. Each with diff, tests, rollback. | **Stop** for Zuko. Nothing in protected files changes until approved. |
| 5 Wire-up after approval | Apply approved proposals; switch storage from sidecar to DB; enable `picks.json` export for `cleared` picks; remove `editor_only` from Politics/Social/General once Gate 0 is live and tested. | Safety test set (§11) passes 100%; `picks.json` contains no never-list story. |
| 6 Feedback & tuning | `feedback-template`, `apply-feedback`, `filter_metrics.md`. Two-week editor trial. | Precision ≥ 70%, unsafe = 0, misses reviewed weekly; weights tuned in config. |

## 11. Testing

- Unit tests per new module (prefilter, relevance, content, select, taxonomy, outputs, feedback).
- Golden-day test: a stored 24 h fixture (from Phase 1 fetch) → fixed expected picks.
- **Safety test set:** 60 labelled headlines (30 never-list incl. tricky ones: festival traffic advisories that are fine vs communal clashes that aren't; party rallies vs policy changes; accident news; caste-quota rulings) + 30 safe ones. Gate 0 must exclude all 30 unsafe and keep ≥ 27 safe.
- Quota tests: one huge story can't take more than 1 pick; Bangalore floor honoured; empty categories handled.
- Update `tests/test_inputs.py` counts; keep all existing tests green.

## 12. Success metrics

| Metric | Target | Measured by |
|---|---|---|
| Pick precision (editor says make/maybe) | ≥ 70% | feedback over 2 weeks |
| Unsafe picks exported to seed agent | 0 | safety test set + feedback |
| Category coverage | picks in ≥ 12 of 18 categories on a normal day | filter_report |
| Recall of High-priority developments | ≥ 95% (existing target), ≥ 90% within 24 h | editor log / misses |
| Source health (non-X) | ≥ 90% passing | sources_health.csv |
| Run time | ≤ 35 min | filter_report |
| Picks per run | 20-30 (never padded) | filter_report |

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Unsafe story reaches the seed agent before Gate 0 ships | `editor_only` for Politics/Social/General; `picks.json` exports only `cleared`; fail-closed |
| AI budget exceeded by wider volume | prefilter with per-category caps; topic subsets per batch |
| Google News block or slow runs | native-first, cap new GN rows at 60, existing pacing, report run time |
| Filter too strict (misses good stories) | candidates.csv + missed log; thresholds in config; weekly tuning |
| Bengaluru content drowned by national news | Bangalore quota floor + geography boost |
| One viral story takes over the list | max 1 pick per cluster, 2 per topic, 4 per category |
| Stale feeds (e.g. Moneycontrol stopped updating) | health check + `known_status`; coverage report flags needs at risk |

## 14. Open questions (founder)

1. Picks per day: 25 default — fine, or different per weekday/weekend?
2. Should Bangalore always have 4 picks, or more at launch week?
3. Who marks feedback daily, and by what time do picks need to be ready (e.g. 08:00 IST)?
4. Should the seed agent get only `High` picks or also `Medium`?
5. Any categories to pause at launch (e.g. Politics) regardless of the filter?

## Appendix A: Category → needs

See `needs.csv` (75 rows). Summary: Bangalore 8, Politics 6, Entertainment 6, Tech/AI 5, Stock market 5, Sports 4, Corporate 4, Finance 4, Social life 4, General 4, Travel 4, and 3 each for Education, College life, Relationships, Growth, Health & fitness, Fashion & style, Beauty & grooming.

## Appendix B: Source facts from the 28 Oct live test

271 free feeds tested (122 native, 149 Google News): 215 live, 25 stale, 13 dead, 2 empty, 16 untested (Times of India / ET / Filmfare blocked in the browser). Every need had ≥ 1 live feed. Weighted coverage 85% (full = 1, partial = 0.5). Moneycontrol feeds are stale or dead. Weakest needs (Google News only): Bangalore autos/power/rent/events, Corporate layoffs/pay, College placements, Growth careers, Travel trains/visas, Fashion brands, Beauty skincare safety.
