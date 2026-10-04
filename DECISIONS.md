# Decisions

Where the team has decided something the brief (`BRIEF.md`) does not say, or says differently. **When this file and the brief disagree, this file wins.** Newest first. Each entry says who decided, when, and why.

---

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
