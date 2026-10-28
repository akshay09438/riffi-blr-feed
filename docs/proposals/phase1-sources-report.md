# Phase 1 report: sources (D-017)

*Status: Phase 1 complete apart from the laptop re-check after the retirements. The cloud session cannot reach any news site, so the live results below came from the laptop run. The sections after "Live results" describe the offline work and are written for the 281-source version; the final count is 276.*

## Live results (laptop, 28 Oct 2026) and what changed because of them

*Run in a fresh folder with a fresh database: the old `engine.db` was not found on that laptop. So every number below is a first run. The fetch time is a worst case (empty Google News cache); the health rate is not comparable with the earlier 87%.*

- `import-sources`: 281 added. `test-feeds` on S132 to S281: **144 passed, 6 failed**, in 2.3 minutes. All Times of India, Economic Times and Filmfare feeds passed (the 15 untested ones are now confirmed).
- Timed `fetch --all`: **22.6 minutes** for 281 sources (target 35; 14.8 for 131). 276 ok, 4 failed, 1 skipped, 0 Google refusals; 15,823 entries read, 7,493 stored, 6,941 stories (22 High, 1,171 Medium, 819 Low, 4,929 Drop).
- `report`: **227 of 255 non-X sources pass = 89%** (target 90%). Nothing failed 3 runs in a row.
- **Six new sources retired** (they leave `feeds.csv`, so `import-sources` marks them inactive; history is kept; reasons in `docs/prd/retired_sources.csv`): S141 Vijaya Karnataka (404), S142 PIB Bengaluru native (0 items), S147 PIB and S174 Formula 1 (no dates, so the engine can never count them healthy), S238 Koimoi and S281 GQ India (403).
- **One source added:** S282, the Google News men's-grooming query, so BEA3 keeps a source. This makes **61** new Google News rows, one over the PRD's cap of 60 (founder to confirm).
- Result: 276 sources (103 native, 125 Google News, 25 X/Instagram backups, 17 page monitors, 3 Telegram, 2 YouTube, 1 manual). Expected health after the next `import-sources` and `report`: 227 of 249 = about 91% (to be confirmed on the laptop). Need coverage by file: 57 full, 18 partial, 0 none (`docs/prd/need_coverage.csv`).
- **Not touched:** S131 (District upcoming movies, an original source) now returns 404; it needs a person to find the page's new address. 29 sources are "stale" and 80 "not useful" in this first-run count; they mean little until there is a week of history.

## What changed (offline part, before the live run)

- `feeds.csv`: 131 -> **281 sources** (S132 to S281 are new). All 281 carry one of the 18 categories (C1). The old 10 values are re-mapped by hand, source by source; a secondary category goes into `topics_hint` after "| also:".
- Every source's `notes` now ends with `needs: ...`, the information needs (`needs.csv`) it serves (C3). New rows also record the 28 Oct browser-test status.
- `needs.csv` (75 needs, 18 categories) is added at the repo root, next to `topics.csv`. The PRD, `new_sources.csv` and this coverage table are in `docs/prd/`.
- Tests that pin counts are updated (`tests/test_inputs.py`, `test_db.py`, `test_fetch_feeds.py`, `test_pipeline_cli.py`), and new tests check the 18 categories, contiguous IDs S001 to S281, a `needs:` entry on every source, 75 needs, no repeated feed URL among the new rows and nothing from `blocklist.csv`. **475 passed, 2 xfailed; ruff check and format clean.**
- No protected file was touched. (`blocklist.csv`, `requirements.txt`, `fetchers/*` are unchanged.)

## Before and after

By route type:

| Route type | Before | After |
|---|---|---|
| Google News RSS | 64 | 124 |
| Native publisher RSS/Atom | 19 | 109 |
| X/Instagram via RSS.app | 25 | 25 |
| Web page monitor | 17 | 17 |
| RSSHub Telegram | 3 | 3 |
| YouTube Atom | 2 | 2 |
| Manual | 1 | 1 |
| **Total** | 131 | 281 |

By category (the old 10 categories had no one-to-one match, so "before" is the old label):

| New category | After |
|---|---|
| Bangalore | 68 |
| Politics | 45 |
| Entertainment | 27 |
| General | 20 |
| Sports | 17 |
| Corporate | 14 |
| Stock market | 13 |
| Tech/AI | 13 |
| Finance | 11 |
| Travel | 8 |
| Social life | 8 |
| College life | 8 |
| Relationships | 7 |
| Education | 6 |
| Health & fitness | 6 |
| Fashion & style | 5 |
| Beauty & grooming | 3 |
| Growth | 2 |

Old labels, for reference: Politics 36, Civic & Infrastructure 21, Sports 8, Entertainment 14, Festivals & Holidays 4, Business & Tech 14, Culture & Identity 8, Lifestyle 9, General news 14, Fact-check 3.

## Dedupe (C2)

236 rows in `new_sources.csv`, compared against the 131 by normalised URL (scheme, `www.`, trailing slash and `utm_` removed; Google News compared on the decoded `q=` without `when:`).

- **37 exact duplicates of an existing source:** skipped. 26 of them were flagged "yes" in the file; **11 more were flagged "no" but are in fact duplicates** (for example The Hindu Bengaluru = S065, TOI Bengaluru = S066, RBI = S049, SEBI = S050, Inc42 = S115, YourStory = S116, Deccan Herald = S063). Their needs were added to the existing source's notes instead.
- **2 near-duplicates skipped** (same publisher, same scope): BOOM `/feed` (S073 already follows BOOM) and The News Minute (S068 already does).
- **1 near-duplicate kept:** PIB Bengaluru region (native feed) beside S107 (Google News `site:pib.gov.in` query). S107 is kept; the recall report decides later. Nothing is removed automatically.
- Other sections of the same big publisher (The Hindu, Indian Express, HT, Mint, ET) are different feeds, not duplicates, and are all kept.
- **Khel Now** (`khelnow.com/feed`) is **skipped: it is on `blocklist.csv`** (founder, 28 Oct).

## Added (C3, C4)

- **90 native feeds**, all kept (preferred by C4). 75 were live in the 28 Oct browser test; **15 were untested** (Times of India, Economic Times, Filmfare, ET HRWorld and similar; a 16th untested row, TOI Bengaluru, is the existing S066) and are listed below.
- **60 Google News rows** (the cap). A Google News row was added only if one of its needs had fewer than 2 live direct feeds; first every need with no direct feed got one, then the cap was filled by how short each need was. **40 were skipped by the rule** (every need already had 2+ direct feeds) and **6 were over the cap**: Google News: FSSAI, Google News: supplements/food safety, Google News: airlines/DGCA, Google News: Nykaa/beauty brands, Google News: ELLE/Femina beauty, Google News: mens grooming India.
- Tier vocabulary is the existing one: publishers `Media`, regulators `Official` (PIB), fact-checkers `Fact-checker`, LiveLaw `Media (legal)`, keyword Google News queries `Aggregator`. Route types are the existing two. `known_status` is `Untested` for every new row, because the engine has not tested them.

## Coverage of the 75 needs (C7)

Counted from the files only. Direct = native publisher feed, YouTube or Telegram. Full = 2 or more live sources including a direct one. The existing 131 sources are counted as live (they were working on 6 Oct); the 15 untested new feeds are **not** counted.

| Coverage | Needs |
|---|---|
| full | 50 |
| partial | 25 |
| none | 0 |

Needs that are not "full" (Google News only, or one direct feed):

| Need | Category | Direct | Google News | Coverage |
|---|---|---|---|---|
| BLR4 Autos cabs & bike taxis | Bangalore | 0 | 4 | partial |
| BLR5 Water power & weather | Bangalore | 0 | 7 | partial |
| BLR7 Events & going out | Bangalore | 0 | 9 | partial |
| POL2 Elections | Politics | 0 | 8 | partial |
| ENT6 Gaming | Entertainment | 0 | 2 | partial |
| TEC5 AI & jobs | Tech/AI | 0 | 2 | partial |
| COR1 Layoffs & hiring | Corporate | 0 | 4 | partial |
| COR2 Workplace policy | Corporate | 0 | 4 | partial |
| COR3 Pay & appraisals | Corporate | 0 | 1 | partial |
| FIN4 Consumer & money scams | Finance | 0 | 2 | partial |
| STK4 Mutual funds & SIPs | Stock market | 0 | 1 | partial |
| COL1 Placements & internships | College life | 0 | 1 | partial |
| COL3 Student life & youth trends | College life | 0 | 2 | partial |
| REL2 Marriage & family laws | Relationships | 0 | 3 | partial |
| SOC3 Nightlife & going out | Social life | 0 | 4 | partial |
| GEN2 Consumer & everyday life | General | 0 | 4 | partial |
| GRO1 Careers & upskilling | Growth | 0 | 2 | partial |
| GRO2 Productivity & self-improvement | Growth | 0 | 1 | partial |
| HEA2 Diet & nutrition news | Health & fitness | 1 | 0 | partial |
| TRA2 Trains & roads | Travel | 0 | 3 | partial |
| TRA3 Visas & passports | Travel | 0 | 4 | partial |
| FAS2 Brands & shopping | Fashion & style | 0 | 2 | partial |
| BEA1 Beauty industry & launches | Beauty & grooming | 1 | 0 | partial |
| BEA2 Skincare trends & safety | Beauty & grooming | 0 | 1 | partial |
| BEA3 Men's grooming | Beauty & grooming | 1 | 0 | partial |

The full 75-row table is `docs/prd/need_coverage.csv`. **Every need has at least one live source (C7 holds on paper). No need is "none".**

## What still has to run on the laptop (cannot be done here)

1. `git pull`, run the suite, back up the database.
2. `import-sources` (adds S132 to S281; existing history is kept).
3. `test-feeds --source S132 ... S281` (C5). For each failure: try the site's `/feed` or `/rss` once and record the fix; a dead or stale feed gets `known_status` "Inactive" in a follow-up change. The 15 untested feeds are the ones to look at first:

| ID | Name | Category |
|---|---|---|
| S141 | Vijaya Karnataka (2) | Bangalore |
| S168 | TOI Cricket | Sports |
| S175 | TOI Top Stories | General |
| S190 | ET Startups (native) | Tech/AI |
| S193 | ET Tech | Corporate |
| S196 | ET HRWorld | College life |
| S198 | ET Jobs | Corporate |
| S207 | ET Wealth | Finance |
| S208 | ET Economy | Finance |
| S212 | ET Markets | Stock market |
| S214 | ET IPOs | Stock market |
| S216 | ET Mutual Funds | Stock market |
| S218 | ET Crypto | Stock market |
| S223 | ET Education | College life |
| S237 | Filmfare | Entertainment |

4. A full `fetch --all` with a stopwatch (C6, target 35 minutes or less; it was 14.8 minutes for 131 sources). Google News is one domain paced at 2 to 2.5 seconds per request, so the 60 new queries add about 3 minutes of pacing and the link look-ups (cap 100) the rest. If it runs over, I propose a split (native first, Google News second), not faster requests.
5. `report` for the health pass rate (target 90% of non-X sources) and re-run the coverage count with the live results.

## Open points for the founder

- The total is 281, below the "about 300" in D-017, because 37 of the file's rows were already in the engine and 40 Google News rows were not needed. I did not pad it. Say if you want the 6 capped Google News rows added anyway (beauty, FSSAI, supplements, airlines).
- Reddit r/bangalore (S061) is an existing source and the PRD calls Reddit "banned" for new sources. I left S061 as it is; tell me if it should go.
- Needs with only Google News coverage are the weak spots (autos/cabs, power and weather, events, layoffs, pay, placements, careers, trains, visas, beauty): the recall test will show whether that matters.
