# Phase 2: categories, needs and topics (D-017): proposal for the founder

*Status: **waiting for your yes (PRD step T5).** Nothing in `topics.csv` or `config/topic_keywords.yaml` has been changed. What is built and pushed: `config/categories.yaml` (the 18 categories with the daily quotas) and `riffi_ingest/taxonomy.py` with its tests. What needs your yes: the changes to the topic list below.*

## 1. What I built (no approval needed)

- `config/categories.yaml`: the 18 categories with their pick quotas (Bangalore 4, Politics max 3, others 1 to 4) and the "editor only until the safety filter exists" flag on Politics, Social life and General.
- `riffi_ingest/taxonomy.py`: reads categories, `needs.csv` and `topics.csv` and answers "which topic serves which need". Its `validate()` reports, in plain sentences, a need without a topic, a topic without a need, an unknown category, or a need without a source. 8 tests; the whole suite passes.

## 2. What I want to change in the topic list (needs your yes)

### 2a. Every topic gets one of the 18 categories, and the needs it serves (T1, T2)

All 151 existing topics stay. None is deleted or renamed. Only three things are added to `topics.csv`: `category` becomes one of the 18 (the old label is kept in a new column `old_category`), `need_ids` says which needs the topic serves, and `editor_only` marks the topics D-017 keeps away from the seed agent. The importer reads columns by name, so nothing in the database code changes. The full table is `docs/proposals/phase2/topic_mapping.csv`.

How the old labels move (you said "keep the 30 Politics topics": they all stay, but 9 of them get a different label: 8 move to **Bangalore** because they are civic or Karnataka-policy topics, such as the GBA polls, guarantee schemes and Shakti, and 1, women in Karnataka politics, moves to Social life. 21 stay Politics):

| Old category (8 kinds) | New category | Topics |
|---|---|---|
| Business & Tech | Corporate | 10 |
| Business & Tech | Tech/AI | 6 |
| Business & Tech | General | 2 |
| Business & Tech | Finance | 1 |
| Business & Tech | Social life | 1 |
| Business & Tech | Growth | 1 |
| Business & Tech | Education | 1 |
| Civic & Infrastructure | Bangalore | 20 |
| Civic & Infrastructure | Social life | 1 |
| Civic & Infrastructure | Health & fitness | 1 |
| Civic & Infrastructure | Tech/AI | 1 |
| Culture & Identity | Social life | 6 |
| Culture & Identity | Bangalore | 5 |
| Culture & Identity | Education | 1 |
| Culture & Identity | Entertainment | 1 |
| Entertainment | Entertainment | 17 |
| Festivals & Holidays | General | 6 |
| Festivals & Holidays | Bangalore | 5 |
| Lifestyle | Bangalore | 7 |
| Lifestyle | Social life | 6 |
| Lifestyle | Finance | 3 |
| Lifestyle | Relationships | 3 |
| Lifestyle | Health & fitness | 1 |
| Lifestyle | Travel | 1 |
| Lifestyle | Education | 1 |
| Lifestyle | College life | 1 |
| Lifestyle | Stock market | 1 |
| Politics | Politics | 21 |
| Politics | Bangalore | 8 |
| Politics | Social life | 1 |
| Sports | Sports | 11 |

### 2b. Topics marked editor only (21 of 151), per D-017

| ID | Topic | Needs | Why |
|---|---|---|---|
| B21 | Kannada vs Hindi; 60% signage; learn Kannada | BLR8 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| B22 | What makes someone a Bengalurean? (locals vs migrants) | SOC1 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| B43 | Old Bengaluru festivals: Karaga, Kadalekai Parishe, Lalbagh  | BLR7 | Religious festival content stays with the editor; heritage and city-event logistics only (D-017) |
| B44 | Festival eco debates: crackers, lake immersion, noise | GEN4 | Environmental angle only; religious content stays with the editor (D-017) |
| K03 | Local job quota for Kannadigas | BLR8 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| K04 | Caste survey and reservation share | POL3; BLR8 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| K05 | Power politics: CM vs rivals, high command, 2028 | POL3 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O02 | CM D.K. Shivakumar: Bengaluru-first agenda and viral remarks | POL3 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O03 | DKS cabinet: 19 ministers inducted 3 Aug, no women ministers | POL3 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O04 | H.D. Kumaraswamy's offensive: HMT land, NICE project, KPSC r | POL3 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O05 | Caste survey report (received 27 May; seeks OBC quota hike t | POL3; BLR8 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O14 | Kannada vs Hindi; 60% Kannada signage rule | BLR8 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O15 | North vs South / locals vs migrants; Bengaluru vs Hyderabad  | BLR8 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O18 | Local job quota for Kannadigas in private / IT firms (2024 b | BLR8 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O23 | Darshan murder trial (day-to-day hearings; more accused seek | ENT5; POL4 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O26 | Cockroach Janta Party (CJP): Gen Z anti-establishment moveme | POL3 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| O27 | Annamalai's new ‘We The Leaders’ movement (quit BJP 5 June) | POL3 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| P05 | Mental health and burnout at work | GRO2; COR2 | Workplace angle only; mental-health or medical advice stays with the editor (D-017 never-list) |
| P15 | Reservation and merit (EWS, creamy layer, private quotas) | POL1 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| P24 | Sport and politics (e.g., playing rivals amid tensions) | SPO1 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |
| SI03 | Three-language policy and Hindi in schools | EDU2 | caste, language / locals-vs-migrants, or leader / party politics (the 4c lists) |

Please look at these, which I added beyond the safety proposal's list (that list had O02-O05, O26, O27, K03-K05, B21, B22, SI03, O18, P15). O14 (Kannada vs Hindi) and O15 (locals vs migrants) follow the same rule as their B and K twins, so I added them. The rest need your call:
- **O23 Darshan murder trial**: a murder case with a named victim, so it falls under "crime victims" in the never-list. Editor only.
- **P05 Mental health and burnout at work**: kept to the workplace angle, but it touches the "illness and medical advice" never-list. Editor only.
- **P24 Sport and politics**: the Indo-Pak match kind of story. Editor only.
- **B43 Old Bengaluru festivals** and **B44 Festival eco debates**: religious festival content. Editor only. The holiday topics (D03, D10, D15, D18, D29, K07, K08, B42, D34) are not editor only, but their `sensitive_note` will say "logistics only: holidays, travel, closures, sales, city events; no religious content".

### 2c. 30 new topics so every one of the 75 needs has a topic (T2)

Before these, 18 of the 75 needs had no topic at all. With them, **every need has at least one** (checked by script). All are pan-India evergreen topics, except B45 (Bengaluru power cuts). `local: false` except B45 (T3). None is on the never-list (T4); the topics that sit close to it carry a safety note (scams: no victims; fact-checks: no religion, caste, party or named people; beauty and food: no medical advice; fashion: no body remarks).

| ID | Category | Need | Topic | Priority | The debate |
|---|---|---|---|---|---|
| B45 | Bangalore | BLR5 | Power cuts, BESCOM tariffs and rooftop solar | Medium | Are BESCOM outages acceptable for a tech city? |
| P27 | Politics | POL4 | Court rulings that change daily life | Medium | Does the ruling help or hurt the ordinary user? |
| P28 | Politics | POL6 | Viral claims, fact-checked | Medium | Why do fakes spread faster than the fix? |
| P29 | Sports | SPO4 | Beyond cricket: Olympics, chess, badminton, F1, kabaddi | Medium | Why does cricket get all the money? |
| P30 | Sports | SPO3 | Football for Indians: ISL, European leagues, the national team | Low | Can Indian football ever matter? |
| P31 | Entertainment | ENT6 | Gaming and esports: BGMI, game launches, bans | Medium | Is gaming a career or a waste of time? |
| P32 | Tech/AI | TEC1 | AI tools and model launches: ChatGPT, Gemini, Claude, Indian AI | High | Will AI make you better or just lazier? |
| P33 | Tech/AI | TEC3 | Phone and laptop launches and price wars | Medium | Is the new iPhone worth it? |
| P34 | Tech/AI | TEC4 | App outages, privacy and new digital rules | Medium | Is India ready for a UPI outage? |
| P35 | Finance | FIN2 | Everyday prices: petrol, LPG, gold, inflation | High | Is gold still a good buy? |
| P36 | Finance | FIN3 | Credit cards, EMIs, BNPL and loan rates | Medium | Credit card for rewards: smart or trap? |
| P37 | Finance | FIN4 | Scams and frauds: digital arrest, UPI fraud, fake apps | High | Whose fault is a scam: the victim or the platform? |
| P38 | Stock market | STK1 | Sensex, Nifty and big market moves | Medium | Is this the time to buy or wait? |
| P39 | Stock market | STK4 | Mutual funds and SIPs for beginners | Medium | SIP vs lump sum: what would you do? |
| P40 | Education | EDU1 | Entrance exams and results: CAT, GATE, NEET-PG, CUET, JEE, paper leaks | High | Are entrance exams fair? |
| P41 | Education | EDU2 | Colleges, UGC rules, fees and rankings | Medium | Is a private college worth the fee? |
| P42 | College life | COL1 | Campus placements, packages and internships | High | Is a high package the only measure? |
| P43 | College life | COL2 | Campus rules, fests and controversies (non-political) | Medium | Attendance rules: reasonable or outdated? |
| P44 | General | GEN1 | Union Budget, Census and big national announcements | Low | What does this change for you? |
| P45 | Growth | GRO2 | Productivity, habits and self-improvement research | Low | Is productivity culture healthy? |
| P46 | Growth | GRO3 | Founder journeys and success stories | Medium | Does hustle culture sell a myth? |
| P47 | Health & fitness | HEA2 | Food safety, FSSAI and what is in your food | Medium | Can you trust labels? |
| P48 | Travel | TRA2 | Trains and roads: Vande Bharat, tatkal, tolls, FASTag | High | Vande Bharat: worth the price? |
| P49 | Fashion & style | FAS1 | Fashion weeks, trends and what is in | Low | Is fast fashion killing style? |
| P50 | Fashion & style | FAS2 | Brands and shopping: Myntra, Zudio, sales, quick fashion | Medium | Zudio vs Zara: who wins? |
| P51 | Fashion & style | FAS3 | Celebrity looks and red carpet style | Low | Best dressed or overdone? |
| P52 | Beauty & grooming | BEA1 | Beauty brands and launches: Nykaa, Mamaearth, Sugar | Low | Are D2C beauty brands worth the price? |
| P53 | Beauty & grooming | BEA2 | Skincare trends, ingredient bans and safety | Medium | Do you need a 10-step routine? |
| P54 | Beauty & grooming | BEA3 | Men's grooming and skincare | Low | Is grooming vanity or hygiene? |
| P55 | Entertainment | ENT3 | OTT releases and what to watch | Medium | Is OTT content better than films? |

Full records (all 13 columns plus needs, 5 to 15 keywords each with Indian spellings and brand names) are in `docs/proposals/phase2/topics_new.csv` and `topic_keywords_new.yaml`.

Weakest fit, for your attention: **P44** ("Union Budget, Census and big national announcements") only exists to give the need "Top national headlines" (GEN1) a topic, so I kept it Low priority with narrow keywords. If you would rather have no such topic, GEN1 stays uncovered and the check will say so.

## 3. After your yes

1. Apply: write the new columns and the 30 rows into `topics.csv`, the 30 keyword entries into `config/topic_keywords.yaml`, and the logistics notes into `sensitive_note`.
2. Update the tests that pin counts (151 topics -> 181; the D/O/evergreen split) and add the real-file test "every need has a topic and a source".
3. Docs: README, technical spec and plan.
4. On the laptop: `import-topics` (151 updated, 30 added), then a `fetch --all`, so the new topics start tagging.

## 4. Questions for you

1. Approve the remap and the 30 new topics as they are, or tell me which to change?
2. Are the extra editor-only calls above right (O14, O15, O23, P05, P24, B43, B44)?
3. Keep P44, or drop it and leave GEN1 uncovered?
