# Decisions

Where the team has decided something the brief (`BRIEF.md`) does not say, or says differently. **When this file and the brief disagree, this file wins.** Newest first. Each entry says who decided, when, and why.

---

## D-011 · Telegram channels are read from their public t.me page (5 Oct 2026, founder)

The brief reads Telegram through RSSHub (`rsshub.app`). On the first live run, rsshub.app answered 403 for all three channels (S016, S101, S102). The founder chose the channels' own public web page instead: `https://t.me/s/<channel>`, which shows the latest posts (about 20) to anyone, with no login. `fetchers/telegram.py` reads the posts from it: text, link and time. The quoted post in a reply is ignored, and posts with no text are skipped. The channel name comes from the row's `fetch_url` (its last path part), so `feeds.csv` and the route type `RSSHub Telegram` stay as they are. Setting `RSSHUB_BASE_URL` (a self-hosted RSSHub) switches back to RSSHub. Telegram is not X, Instagram or WhatsApp, so the never-scrape rule does not apply. The fetch goes through the same polite HTTP client, with the same identity and limits.

A channel page with no posts at all counts as a failure, not as quiet: it means the channel turned off its public preview, or Telegram changed the page. Built and tested in the cloud against a sample page; the real proof is `test-feeds --source S016 --source S101 --source S102` on the laptop.

## D-009 · No timer for now: the engine fetches only when the founder asks (5 Oct 2026, founder)

The Windows timer from D-008 is **not installed**. Do not install it, and do not offer to, until the founder says otherwise. Fetching happens only when the founder asks for it: a session on the laptop runs `python -m riffi_ingest fetch --all` (or `--due`, or `--source ...`) by hand. Everything else in D-008 stays: the speeds in `config/schedule.yaml`, the run diary, `engine.log` and `status`. `scripts/schedule-windows.ps1` stays in the repo, unused, so switching to the timer later is one command.

What this means: `status` will show long gaps and "no checks at all" between the founder's runs. That is expected, not a fault. The two-week test's timeliness figure (caught within 24 h) and its gap attribution assume regular runs, so for the test to measure what the brief asks, fetches need to happen at least daily. If they don't, the day-14 report must say so.

Why: the founder wants to control when the engine reaches out to the sites.

## D-008 · The engine runs on Windows' own timer; Google News every 2 h during the two-week test (5 Oct 2026, founder)

The brief names APScheduler (in its STEP 0, the standalone path; D-001 lists it too) and these speeds ("What the engine must do" step 2 and STEP 7): every 30 min for Google News queries on High-priority topics, Telegram and (in step 2) the priority X feeds; every 2 h for all other feeds; every 6 h for page monitors. STEP 7 also asks for a 07:00 IST digest job; that comes with step 8 (outputs), not here. D-008 narrows the "every 30 min (High-priority) to 2 h" Google News range in D-004 to 2 h for the two-week test.

Instead:
- **Windows Task Scheduler** starts `pythonw -m riffi_ingest fetch --due` (python's windowless pythonw.exe) every 30 minutes (`scripts/schedule-windows.ps1`; the server's own timer after the move, D-003). The engine fetches only the sources whose interval is up, counted from each source's last attempt, so a sleep, reboot or crash costs one catch-up run, never a pile-up. No long-running process and no new library.
- **Speeds** live in `config/schedule.yaml` (team-editable: per route type, with per-source overrides). During the two-week test: Telegram every 30 min; Google News (including the X/Instagram backups), publisher feeds and YouTube every 2 h; page monitors every 6 h.
- **A run diary** (`engine_runs`) records every run, every timer check that found nothing due or found a run still going, and every check that failed before a run could start. One line per check also goes into `data/engine.log`. A run where every source failed to reach its site, across two or more sites, is "offline" and counts against no source. `python -m riffi_ingest status` shows the last run, gaps, Google refusals and failing sources.
- **The timer is installed on the laptop only after this change is merged, and only with the founder's separate yes.** It runs only while the laptop is on and the founder is logged in; if the laptop sleeps, the next check catches up and `status` shows the gap. While it is on, the project folder stays on `main`: the timer runs whatever code is in the folder, every 30 minutes, against the real database, so anything else is tried only after switching the timer off (`scripts/schedule-windows.ps1 -Remove`).

Why: `feeds.csv` does not say which topics a Google News query covers (`topics_hint` is free text), so "Google News feeds tied to High-priority topics" could not be listed. 2 h already meets the 24 h recall target. Fewer requests to Google lower the risk of a block that would end the test (D-004). Before launch, feeds that broke news first during the test can be moved to 30 min in `config/schedule.yaml`. A timer owned by Windows survives reboots and crashes with no window left open.

## D-007 · Claude merges its own pull requests when they are safe to (4 Oct 2026, founder)

A Claude session may merge its own pull request into `main` without waiting for the founder when **both** hold:
- every CI check on the PR's latest commit has passed (verify, secret scan, semgrep, coverage, goodnight gate), and
- the PR changes no file on the dangerous list (`CLAUDE.md` Part B / `.zuko/config.json`).

A PR that touches a dangerous file waits for the founder's explicit OK for that change. Agreeing to start the work counts, if the PR does what was agreed; say so in the PR. A red check is never merged; fix it or ask. One logical change per PR still applies.

Why: the founder is non-technical and reviews by outcome, not diff. Green checks plus the dangerous-list gate are the safety net, so routine work should not sit waiting for a click.

## D-006 · Separate from the existing "Riffi Feeds" project; copy, never import (4 Oct 2026, founder)

The existing project at `C:\Users\Akshay\Projects\Riffi feeds` (the "panel") stays separate and keeps running as it is. This engine may **copy** code from it (with tests), but must never import from it, share its database, or change anything in that folder. The reuse map is in `docs/implementation-plan.md`.

Why: the panel is in daily use with its own founder rules; coupling the two would make a change in one break the other.

## D-005 · AI tagging runs in Claude Code on the founder's plan, not the Anthropic API (4 Oct 2026, founder)

The brief's step 4 says "LLM pass with the Anthropic API (model and API key from env vars)". Instead, the AI pass (topic IDs, is-new-development, what's new, debate angle, excluded verdict) runs inside a Claude Code session on the founder's Claude plan: the engine writes the batch of clusters waiting for tagging to a file, a Claude Code session tags them (20 per batch, as the brief says), and the engine reads the answers back and validates them. No API key, no pay-per-use bill.

Consequences:
- The AI pass happens only when a Claude Code session runs it (planned: a scheduled task in the Claude desktop app shortly before 07:00 IST, approved once by the founder). The laptop must be awake and the app signed in.
- The 07:00 digest must still be produced if the AI pass has not run (plan usage limits, laptop asleep): keyword tags only, with those clusters marked "awaiting AI pass". Exact behaviour is an open question in the implementation plan.
- The keyword pass and a keyword-level exclusion pre-filter matter more, because they are the only safety net when the AI pass is late.

## D-004 · Fetching follows the brief, not the panel project's honest-bot rule (4 Oct 2026, founder)

The panel project's founder rules (28-29 Sep 2026) are: always identify as "RiffiFeedBot", never imitate a browser, honour robots.txt, and use Google News only for three thin topics at most every 3 hours, with links not resolved. **This engine deliberately does not follow those rules.** It follows the brief: browser-like User-Agent, Google News for the 60 query feeds and as the backup for the 25 X/Instagram rows, polled every 30 min (High-priority) to 2 h, with Google News links resolved to the real article.

Known and accepted when deciding: `news.google.com/robots.txt` disallows `/rss` for bots, which is why the panel project switched Google News off. A block by Google or a publisher is not undone by reverting code.

Guardrails kept because of that risk:
- The fetch-etiquette code (identity, timeouts, retries, per-domain rate limit of 1 request per 2 s, Google News resolution) is on the dangerous list: no change without the founder's OK.
- Every Google News resolution is cached and resolutions per run are capped (lesson from the panel project).
- Never scrape X, Instagram or WhatsApp directly (brief, unchanged).

## D-003 · Runs on this laptop for the two-week test (4 Oct 2026, founder)

Hosted on the founder's Windows laptop for now; move to a small always-on server before launch. The dashboard and the `/api/stories` endpoint listen on `127.0.0.1` only, so they need no login while on the laptop. Moving to a server means adding a login, and the login code joins the dangerous list at that point.

## D-002 · Scale: Bengaluru only, sized as estimated (4 Oct 2026, founder)

A team of 3-10 people plus one AI agent; 131 sources; about 3,000-8,000 new items a day (about 200,000 in month one). Raw feed payloads and page-monitor text are kept 30 days, then pruned. Story summaries and the editor's ground-truth log are kept forever. No multi-city design.

## D-001 · Standalone Python 3.11 service, in its own folder outside OneDrive (4 Oct 2026, founder)

The brief's "STEP 0" path for "no admin panel in this repo": a standalone Python 3.11 service (SQLite, feedparser + httpx, trafilatura, APScheduler, Typer, a minimal FastAPI dashboard). The project lives at `C:\Users\Akshay\Projects\Riffi ingestion engine`, not in OneDrive (OneDrive sync locks git and SQLite files). On this laptop that means Python 3.11 for ARM64 (`py -V:3.11-arm64`); the key wheels (lxml, pydantic-core, RapidFuzz) exist for it.
