# Phase 0: draft D-017 (not yet approved)

*Status: draft for the founder. Nothing in `DECISIONS.md`, `CLAUDE.md`, `.zuko/config.json` or any other file has been changed. This file only holds the proposal. On approval, the D-017 text below goes into `DECISIONS.md` (not on the dangerous list), and the CLAUDE.md change goes to Zuko (dangerous list item 10).*

Source: `PRD_riffi_news_engine.md` section 10, Phase 0. I read the PRD, `needs.csv`, `new_sources.csv`, `DECISIONS.md`, `CLAUDE.md` and `BRIEF.md`.

---

## 1. Draft D-017 (goes at the top of `DECISIONS.md`, newest first)

```markdown
## D-017 · Scope widened to 18 content categories for Riffi's launch audience (28 Oct 2026, founder)

The engine now covers the 18 Riffi content categories (Bangalore, Politics, Sports, Entertainment, Tech/AI, Corporate, Finance, Stock market, Education, College life, Relationships, Social life, General, Growth, Health & fitness, Travel, Fashion & style, Beauty & grooming), not only Bengaluru city news. The audience is English-speaking, tier-1 urban Indians aged 18-28 (college students and young professionals), Bengaluru-first. Plan: `PRD_riffi_news_engine.md`.

- **One audience, no multi-city design.** Bengaluru keeps a scoring boost and a minimum number of daily picks. Nothing is built per city.
- **Sources and topics widen.** About 300 sources (131 today plus new ones from `new_sources.csv`, added in Phase 1) and the topic list grows so every information need in `needs.csv` has at least one topic (Phase 2, with the founder approving the new topics first).
- **The output narrows.** Besides the full audit digest, the engine produces a short daily shortlist ("Daily Picks", about 25 stories) that passed a safety gate, a relevance gate and a content-ability gate. This is the output the content team and the seed agent use.
- **Supersedes D-002** on "Bengaluru only", "131 sources" and the item-volume estimate, for sources and topics. The new estimate is about 8,000 to 18,000 new items a day (about 1,500 to 3,000 stories). Retention (raw payloads 30 days, summaries and ground-truth log kept forever) and "3-10 people plus one AI agent" are unchanged.
- **Does not change:** D-004 (browser-like fetching, Google News risk accepted), D-005 (AI tagging on the founder's plan, no API key), D-006, D-007, D-008 (Google News every 2 h, 1 request per 2 s per domain), D-009 (timer off, runs only on request), D-016 (`/api/stories` not before the safety filter). The engine still never posts, replies or republishes.
- **Safety first.** Until the safety filter (Gate 0) is built, approved by Zuko and passes its test set, Politics, Social life and General picks are for the editor only and never exported to the seed agent.
- **Protected files stay protected.** Every change to a dangerous-list file is written as a proposal in `docs/proposals/` and waits for the founder, as before.
- **Open points the draft cannot decide for the founder:** see section 3 of `docs/proposals/phase0-d017.md`.
```

## 2. The CLAUDE.md change for Zuko

The PRD asks for "the one-line CLAUDE.md change". In practice one line is not enough, because the old scope appears in several places, and the JSON block there is what Zuko's hooks and `.zuko/config.json` are generated from. All of these are on the dangerous list (item 10: the rules themselves), so I have not touched them. Proposed edits, for Zuko and the founder:

| Where in `CLAUDE.md` | Today | Proposed |
|---|---|---|
| Part B "What this is and why" (first paragraph) | "tracked 151 topics from 131 sources" | "tracked topics across the 18 Riffi content categories from about 300 sources"; add one clause: "scope widened by D-017" |
| Product DNA, "Who it is for" | launch audience: "tier-1 Bengalureans earning Rs 1-2L+ a month" | "English-speaking tier-1 urban Indians aged 18-28, Bengaluru-first" |
| Product DNA, "Non-goals" | "Bengaluru only in phase 1; no multi-city design." | "One audience; no multi-city design. Bengaluru-first within 18 categories (D-017)." |
| Product DNA, "Expected scale" | "131 sources, 151 topics; about 3,000-8,000 new items a day (about 200,000 in month one)" | "about 300 sources; about 8,000-18,000 new items a day (about 240,000-540,000 in month one)"; keep the pruning and indexing sentences |
| The `config` JSON block: `summary`, `product.audience`, `product.nonGoals`, `product.scale` | same wording as above | the same changes, kept in agreement with the prose |
| `.zuko/config.json` | generated from that block | regenerate after the above, never hand-edit |

Also: the "Known input gaps" list and the `docs/technical-spec.md` / `BRIEF.md` counts (131, 151) go stale. Those are not on the dangerous list, so they are updated in the same changes that alter the counts (Phases 1-2), as working rule 8 requires.

## 3. Things the founder should decide or know before approving

**A. The PRD's never-list contradicts the brief on three points.** The PRD says `DECISIONS.md` wins and to stop on a conflict, so I am stopping here on these.

| Subject | `BRIEF.md` (line 53-55) | PRD section 6.3, Gate 0 |
|---|---|---|
| Caste survey and reservation | "Sensitive but allowed": flagged for a human | Never-list: dropped permanently |
| Kannada vs Hindi, locals vs migrants | "Sensitive but allowed": flagged | `editor_only`, never exported to the seed agent (a middle path, but stricter than flagged) |
| Religion | Only communal or religious flashpoints are excluded | "Religion and religious practice" in general is excluded; the topics list has 11 Festivals & Holidays topics and the PRD's own need GEN4 (festivals and holidays) |
| Party politics | National politics "without communal framing" is wanted | "Party-vs-party politics; for/against a named politician or party" excluded; 30 existing topics are Politics, and the existing O02-O04 topics are about named leaders |

My recommendation: D-017 states that for the picks and the seed agent, the PRD's stricter never-list applies, because it is the later and more careful rule, and that the brief's "sensitive but allowed" subjects are still tracked and still reach the editor as `editor_only`. That needs your yes, because it removes some topics from what the seed agent can see. The festival and holiday needs (GEN4) need an explicit line: a holiday list or traffic advisory is fine; a row over a festival is not.

**B. The PRD changes the scoring numbers.** Section 6.3 gives Bengaluru 20 points; `config/scoring.yaml` gives 25. That belongs to Phase 3 and does not block D-017.

**C. Blocklist clash for Phase 1.** `new_sources.csv` row "Khel Now" (`khelnow.com`) is on `blocklist.csv` ("Venue error found"), which is a dangerous-list file. Unless you lift it, the engine would drop everything from that feed. I will list it in the Phase 1 report and ask rather than change it.

**D. Google News load.** `D-004` / `D-008` pace Google News at 1 request per 2 s per domain, 100 link look-ups per run. Today 64 sources use Google News, plus the 25 X/Instagram backups. The PRD adds up to 60 more Google News queries (cap in C4), and aims for a full run under 35 minutes (C6) against 14.8 minutes today for 131 sources. Google News is one domain, so its queries run one after another; 60 more queries add about 2.5 to 3 minutes of pacing, but the extra link look-ups could add much more. Phase 1 must measure this. Nothing in D-017 raises any request rate.

**E. Timeline.** Launch is about a week out and the PRD has seven phases. Phases 0-3 do not touch protected files; Phase 4 waits for Zuko. D-017 does not itself promise a date. If the launch date is firm, tell me which phases must be done by then and I will plan around that.

## 4. What I need from you

1. Approve, change or reject the D-017 text in section 1.
2. A decision on point A (recommendation above).
3. Your OK on the CLAUDE.md edits in section 2, so Zuko can apply them.

After your approval I will add D-017 to `DECISIONS.md` on branch `main-nb68vk` and start Phase 1 (sources). I will not start before then.
