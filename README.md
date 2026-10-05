# Riffi news ingestion engine

Collects new developments on Riffi's 151 tracked topics from 131 sources, cleans them, groups them into stories and tags them by topic. It only collects, ranks and reports: it never posts anything. What it does and why: `BRIEF.md`; where the team decided differently: `DECISIONS.md`; how it is built: `docs/technical-spec.md`; how far along it is: `docs/implementation-plan.md`.

**Built so far:** fetching every route, cleaning, Google News link resolution, the blocklist, grouping into stories, keyword tagging, scoring and labels, the database, the scheduler (`fetch --due`, which fetches each source at its own speed, the run diary and the `status` check are tested; the Windows timer that runs it every 30 minutes is written and checked but has never been registered, and is only switched on after the founder says yes, see "Running on its own"), and the commands below. **Not yet:** the AI tagging pass, the 07:00 digest, the dashboard and `/api/stories`, and the two-week recall test.

## Setup (Windows laptop, once)

In PowerShell, inside this folder (`C:\Users\Akshay\Projects\Riffi ingestion engine`, which is outside OneDrive on purpose):

```powershell
py -V:3.11-arm64 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then every command below is run as `.venv\Scripts\python.exe -m riffi_ingest <command>`.

**Note on PyYAML (Windows on ARM64).** PyYAML 6.0.3 has no ready-made package for this laptop's chip, so pip builds it while installing and leaves out its optional speed-up written in C. That is expected and nothing needs fixing: the engine works the same, and the config files are far too small for the speed to matter. In a cloud (Linux) session, if pip says it cannot uninstall a PyYAML that came with the system, run `python3 -m pip install --ignore-installed pyyaml==6.0.3` first.

## Commands

| Command | What it does |
|---|---|
| `test-feeds` | Fetches every source once and prints a pass / fail table (HTTP status, items, newest item, fields) with a suggested fix for each failure. Also writes it to `reports/test-feeds/<date-time>.csv` and `.md`. Stores nothing. Add `--source S004` (repeatable) to test a few. Takes about 3-4 minutes for all 131: each site gets at most one request every 2 seconds, and 84 sources are on Google News. A progress line shows each source as it finishes. |
| `import-sources` | Loads `feeds.csv` into the database. Safe to run again after editing the CSV: rows are updated, history is kept, and a removed source is marked inactive, never deleted. |
| `import-topics` | Loads `topics.csv` into the database. Safe to run again. |
| `fetch --all` | One full cycle for every active source: fetch, clean, group into stories, tag, store. `fetch --source S004 --source S011` does just those. `fetch --due` does only the sources whose time is up (speeds in `config/schedule.yaml`): this is what the Windows timer runs every 30 minutes. Say exactly one of `--all`, `--source`, `--due`. The first `fetch` imports the sources by itself. Takes up to about 12 minutes for everything (the source fetches plus up to 100 Google News link look-ups); please leave it running. Only one fetch can run at a time. Every run, by hand or by the timer, goes into the run diary and one line into `data/engine.log`. |
| `stories` | The best stories of the last 24 hours, highest score first: score, label (High / Medium / Low / Drop), number of sources, topics and headline. `--top 50`, `--hours 48`, `--label High`. Until the AI pass exists, scores leave out its 25 points and stories show `[awaiting AI]`. |
| `status` | Is the engine alive? Prints the last run, what happened in the last 24 hours (checks by outcome, the longest gap with no check, Google refusals) and the sources failing 3 or more runs in a row. Changes nothing, and is safe to run at any time, even while a fetch is running. |
| `log-template` | Creates the editor's empty daily log, `data/ground_truth.csv`, and `data/ground_truth_topics.csv`, the list of the High-priority topics to log from (rewritten each time, so it follows `topics.csv`). It never overwrites a log that already exists. |
| `check-log` | Checks the editor's log row by row: dates, times, topic ids, empty cells and repeats. Each problem names its row. Changes nothing. |

Every command has `--help`.

## The first run (BRIEF.md "FIRST RUN")

1. `test-feeds` and read the failures and their suggested fixes. Known ones before any run: S047 and S107 have an instruction where the URL should be, S108 has no backup Google News URL, S121 needs a YouTube channel ID.
2. Fix what you can in `feeds.csv`, then `import-sources`.
3. `fetch --all`, then `stories` to see the top 30 with their scores and topics (a dashboard comes in step 8).

## Running on its own (the two-week test)

**What the timer does.** Every 30 minutes Windows starts `fetch --due`. The engine fetches only the sources whose time is up, counted from each source's last attempt (D-008):

| Kind of source | Fetched |
|---|---|
| Telegram | every 30 minutes (at every check) |
| Google News (including the X and Instagram backups), publisher feeds, YouTube | every 2 hours |
| Web page monitors | every 6 hours |

After the laptop has been asleep or off, the next check fetches everything that is overdue once, then each source goes back to its own speed. Two runs never overlap: a check that finds one still going is skipped and noted. If the internet is down and no site can be reached, the run is marked "offline", no source is blamed, and they are all tried again at the next check. (One limit: a check where only the three Telegram sources are due cannot tell "no internet" from "t.me is down", so those three take a strike each time; after three such checks they show under "failing 3+ runs".)

**Not in use for now (D-009).** The founder decided on 5 Oct 2026 that the engine fetches only when asked (`fetch --all` by hand), so the timer is not installed and must not be until that decision changes. Expect `status` to show gaps between runs. The rest of this section is for when the timer is switched on.

**Switching it on.** The timer is **not installed**. The engine side (`fetch --due`, the run diary, `status`) is tested. The Windows task script has been checked (it parses, its settings were built and read back in memory, and `pythonw` ran `fetch --due` with no window) but it has never been registered with Windows, so registering it is its real test. After this work is merged, Zuko installs it with the founder's yes: `powershell -ExecutionPolicy Bypass -File scripts\schedule-windows.ps1`. To stop it, run the same command with `-Remove` on the end. It runs only while the founder is logged in to Windows (no password is stored), also on battery, with no window.

**Keep this folder on `main` while the timer is on.** Windows' timer runs whatever code is in this folder every 30 minutes, against the real database. Do not try out other branches or half-finished code here. To try something, turn the timer off first with `scripts\schedule-windows.ps1 -Remove` (Zuko does this) and switch it on again afterwards.

**Timer on or off.** `config/schedule.yaml` says `timer: off` for now (D-009), so `status` treats gaps between hand-run fetches as expected and does not warn about them. Set it to `timer: on` only when the Windows timer is installed.

**Changing the speeds.** Edit `config/schedule.yaml` (a speed per kind of source, written `30m`, `2h`, `6h` or `never`, plus per-source overrides under `sources:`). It is read at every run, so there is nothing to restart. Nothing can go faster than every 30 minutes. Ask before speeding up Google News: more requests to Google raise the risk of a block, and a block is not undone by reverting code (D-004).

**Checking on it.** Run `status`. Reading it:
- *Last run* says when the engine last fetched, how long it took and how many sources worked. If it says **FAILED**, **"no internet"** or **"did not finish"**, or if *Last 24 hours* says there were **no checks at all**, do not try to fix it by hand: tell Zuko (or run `/zuko:fix`). "No internet" on its own usually clears itself at the next check; "did not finish" means the run was stopped or crashed; no checks at all means the timer is off or the laptop was off.
- *Longest gap with no check* appears when the engine was silent for more than 45 minutes: the laptop was off or asleep, or the timer was not running. "No gaps" is the good answer.
- *Google refusals* should be 0. Any other number means Google has started refusing: stop and ask Zuko before fetching more. To stop the timer, Zuko runs `scripts\schedule-windows.ps1 -Remove`.
- *Sources failing 3+ runs in a row* are the candidates for replacement. The engine never switches or deletes a source by itself; the day-14 report only recommends.

`data/engine.log` has one line for every check (and a detail under a failure). It sits beside the database.

**The laptop.** For the two weeks, keep it plugged in and set to not sleep while on mains power (a Windows setting only its owner changes: Settings, System, Power & battery). Closing the lid usually puts the laptop to sleep, so leave it open (or change what the lid does in the same Windows settings). If it does sleep, the engine catches up at the next check after it wakes and `status` shows the gap, so the day-14 results can be read knowing when the engine was not watching.

## The editor's daily log (the two-week test)

The day-14 keep / fix / drop verdicts are scored against this log, so it is set up before day 1.

1. Run `log-template` once. It creates `data/ground_truth.csv` and the topic list `data/ground_truth_topics.csv`. Both open in Excel. The `data/` folder stays on this laptop and never goes to GitHub.
2. Each day, the editor spends about 10 minutes adding one row for each real development on a High-priority topic, wherever they saw it (X, WhatsApp, TV, friends):

   | date | topic_id | what_happened | where_seen | time_seen |
   |---|---|---|---|---|
   | 2026-10-12 | P01 | BBMP floats the tunnel road tender | X | 09:15 |

   `time_seen` is IST on the 24-hour clock. Excel's own date style (12-10-2026) and "9:15 AM" also work. Save as **CSV UTF-8** so Kannada text survives. A Google Sheet works too: download it as CSV into the same place.
3. Run `check-log` after saving. "Fix" lines must be corrected, because a wrong topic id or time would count against a source. "Note" lines are fine: a repeat is counted once, and a topic that is not High priority is kept but the test scores High ones.

Matching the log to stories and the recall report come with step 10. They read this same file.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `RIFFI_DB_PATH` | `<this folder>\data\engine.db` | The database file. Never put it inside OneDrive (D-001): the engine refuses to. `engine.log` and the run lock (`fetch.lock`) sit beside it. |
| `RSSHUB_BASE_URL` | not set | Not set: Telegram channels are read from their public page, `t.me/s/<channel>` (D-011). Set it to a self-hosted RSSHub to read them from there instead. |
| `ZUKO_SLACK_WEBHOOK_INGEST` | (none) | For the Zuko escalation hook; a secret, set on the machine, never written in a file. |

No API keys are needed: AI tagging will run in Claude Code on the founder's plan (D-005).

## Files the team edits

| File | What it is | Notes |
|---|---|---|
| `feeds.csv` | The 131 sources (an export of Source List v4) | After editing, run `import-sources`. |
| `topics.csv` | The 151 topics | After editing, run `import-topics`, and give a new topic keywords (below). |
| `config/scoring.yaml` | The relevance-score weights and label cut-offs | Tune after the two-week test. |
| `config/schedule.yaml` | How often each kind of source is fetched, plus per-source overrides | Read at every run; no restart. A speed the engine cannot read stops the timed runs (a line in `data/engine.log`) and `status` warns. |
| `config/topic_keywords.yaml` | 5-15 keywords per topic for the keyword pass | Plain phrases; the rules are at the top of the file. A test fails if a topic in `topics.csv` has no keywords. |
| `blocklist.csv` | Copycat and unreliable sites; anything from them is dropped | Protected file: changes need the founder. Put the site's domain in the row; a row without one is reported, never guessed. |

### Adding a source

Add a row to `feeds.csv` with a new `source_id` and one of the route types: `Native publisher RSS/Atom`, `Google News RSS`, `X/Instagram via RSS.app` (fill `backup_google_news_url`; X and Instagram are never fetched directly), `RSSHub Telegram`, `YouTube Atom` (a `https://www.youtube.com/feeds/videos.xml?channel_id=...` URL), `Web page monitor`, or `Manual`. Then `test-feeds --source <id>` and `import-sources`.

### Adding a topic

Add a row to `topics.csv`, then an entry with the same `topic_id` in `config/topic_keywords.yaml` (label, 5-15 keywords, `local: true` if the keywords are generic and the topic is about Bengaluru / Karnataka), then `import-topics`.

## For developers

- Tests: `.venv\Scripts\python.exe -m pytest -q tests`; lint: `-m ruff check .` and `-m ruff format --check .` (`node .claude/hooks/py.js ...` picks the right Python on the laptop or in the cloud).
- Rules for AI agents working here, including the list of protected files: `CLAUDE.md`.
