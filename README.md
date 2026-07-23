# RENPHO Weekly-Average Tool

Pulls your RENPHO smart-scale history into a local SQLite database and generates a weekly-average report (weight, body fat %, muscle %, and other body-composition metrics), with week-over-week deltas.

Built defensively around `renpho-api`, an unofficial, reverse-engineered client (v0.1.0, ~6 GitHub stars at the time of writing) — it can break without warning if Renpho changes their backend. SQLite is the source of truth: once a reading is fetched, it lives locally forever, so the tool keeps working on existing history even if the API dies.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Client library is Python |
| Data fetch | [`renpho-api`](https://github.com/danvaneijck/renpho-api) | Unofficial client |
| Storage | SQLite (stdlib `sqlite3`) | Zero setup, survives API breakage |
| Analysis | pandas | Weekly resampling in one line |
| Output (MVP) | Rendered HTML file | Double-click to open, no server |
| Secrets | `.env` via `python-dotenv` | Credentials never touch git |
| Scheduling | `launchd` (macOS), daily | Local, free, credentials never leave the machine |
| Notifications | Gmail SMTP via stdlib `smtplib` | No new dependency, no paid service |

## Architecture

```
fetcher.py  -> pulls + normalizes measurements from the Renpho cloud API
store.py    -> SQLite, idempotent upsert, outlier flagging (source of truth)
analysis.py -> pandas weekly resample (Sun-Sat) + week-over-week deltas
report.py   -> writes report.html
notify.py   -> builds + sends the weekly summary email
```
`run.py` chains fetch -> store -> report -> open, for manual use. `weekly_check.py` chains fetch -> store -> notify, for the automated daily job -- see Automation below. If `renpho-api` ever breaks, only `fetcher.py` needs to change — everything downstream just reads from SQLite.

## Setup

```
python3 -m venv venv
venv/bin/pip install "renpho-api[dotenv]" pandas
```

Create a `.env` file (gitignored) with:
```
RENPHO_EMAIL=your_email@example.com
RENPHO_PASSWORD=your_password

# For the weekly email (see Automation below) -- EMAIL_APP_PASSWORD is a
# Google *app password* (myaccount.google.com/apppasswords, requires 2FA),
# not your real Gmail password. EMAIL_TO is optional; defaults to EMAIL_ADDRESS.
EMAIL_ADDRESS=your_gmail@gmail.com
EMAIL_APP_PASSWORD=your16charapppassword
```

## Usage

```
venv/bin/python run.py
```
Syncs new measurements from Renpho, upserts them into `renpho_data.db`, regenerates `report.html`, and opens it in your browser. If Renpho is unreachable or breaks, it falls back to reporting on whatever's already synced instead of crashing -- see `GOAL_MODE` and `GOAL_ANCHOR_WEEK_ENDING` at the top of `report.py` if you want to adjust the cut/bulk framing or set your target-trend start date from code instead of the report's own "Start date" picker.

## Automation

`weekly_check.py` runs headless (no browser, timestamped log lines instead of `print`): sync with Renpho, then email the summary for the most recently *completed* Sun-Sat week -- but only once. A `notifications` table in SQLite records which weeks have already been emailed, so the job is safe to run daily (catching the completed week whenever the Mac next wakes) instead of needing a perfectly-timed once-a-week trigger.

Scheduled locally via `launchd` (macOS's scheduler) rather than a cloud cron: free, your Renpho/email credentials never leave the machine, and the unofficial API gets called from your home IP where it already works. Trade-off: it only runs when the Mac is on/awake -- fine for a weekly check.

Install (one-time):
```
cp com.renpho.weeklycheck.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.renpho.weeklycheck.plist
```
Runs daily at 9:00 AM by default -- edit the `Hour`/`Minute` in the plist (both the repo copy and the installed copy) to change it. Useful commands:
```
launchctl start com.renpho.weeklycheck   # trigger a run right now, without waiting
launchctl list | grep renpho             # confirm it's loaded
launchctl unload ~/Library/LaunchAgents/com.renpho.weeklycheck.plist   # stop it
```
Logs (including your weight data, so gitignored) land in `logs/weekly_check.log` and `logs/weekly_check.err.log`.

## Design decisions

The non-obvious calls made while building this, and why, are recorded in **[DECISIONS.md](DECISIONS.md)**, grouped by the phase each one was made in.

## Status

- [x] Phase 0 — spike, confirmed real data returns
- [x] Phase 1 — `fetcher.py` + `store.py`, idempotent SQLite ingest (verified: 1553 -> 1553 rows across two consecutive runs)
- [x] Phase 2 — weekly averages (`analysis.py`): Sun-Sat resample, week-over-week deltas, daily dedup, timezone-correct bucketing, verified against the RENPHO app
- [x] Phase 3 — interactive HTML report (`report.py`): inline SVG trend chart (weekly + single-week daily views), sortable/goal-aware table, goal-met check, fixed-anchor target trend line, adjustable + persisted week window, light/dark theming
- [x] Phase 4 — `run.py` entrypoint: fetch -> store -> report -> auto-open, graceful fallback to existing data on any Renpho failure (verified both paths), `test_ingest.py` retired
- [x] Phase 5 — automated weekly email (`weekly_check.py` + `notify.py`): once-per-completed-week guard, `launchd` daily schedule, verified real send + duplicate-prevention end-to-end

**MVP complete, and it now runs itself.** `python run.py` for an on-demand report; the `launchd` job handles the weekly email with no manual steps at all.
