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
| Scheduling | Manual `python run.py` | Once a week, automated later |

## Architecture

```
fetcher.py  -> pulls + normalizes measurements from the Renpho cloud API
store.py    -> SQLite, idempotent upsert, outlier flagging (source of truth)
analysis.py -> pandas weekly resample (Sun-Sat) + week-over-week deltas
report.py   -> writes report.html
```
Chained together by `run.py`. If `renpho-api` ever breaks, only `fetcher.py` needs to change — everything downstream just reads from SQLite.

## Setup

```
python3 -m venv venv
venv/bin/pip install "renpho-api[dotenv]" pandas
```

Create a `.env` file (gitignored) with:
```
RENPHO_EMAIL=your_email@example.com
RENPHO_PASSWORD=your_password
```

## Usage

```
venv/bin/python run.py
```
Syncs new measurements from Renpho, upserts them into `renpho_data.db`, regenerates `report.html`, and opens it in your browser. If Renpho is unreachable or breaks, it falls back to reporting on whatever's already synced instead of crashing -- see `GOAL_MODE` and `GOAL_ANCHOR_WEEK_ENDING` at the top of `report.py` if you want to adjust the cut/bulk framing or set your target-trend start date from code instead of the report's own "Start date" picker.

## Design decisions

The non-obvious calls made while building this, and why, are recorded in **[DECISIONS.md](DECISIONS.md)**, grouped by the phase each one was made in.

## Status

- [x] Phase 0 — spike, confirmed real data returns
- [x] Phase 1 — `fetcher.py` + `store.py`, idempotent SQLite ingest (verified: 1553 -> 1553 rows across two consecutive runs)
- [x] Phase 2 — weekly averages (`analysis.py`): Sun-Sat resample, week-over-week deltas, daily dedup, timezone-correct bucketing, verified against the RENPHO app
- [x] Phase 3 — interactive HTML report (`report.py`): inline SVG trend chart (weekly + single-week daily views), sortable/goal-aware table, goal-met check, fixed-anchor target trend line, adjustable + persisted week window, light/dark theming
- [x] Phase 4 — `run.py` entrypoint: fetch -> store -> report -> auto-open, graceful fallback to existing data on any Renpho failure (verified both paths), `test_ingest.py` retired

**MVP complete.** `python run.py` is the one command that does everything.
