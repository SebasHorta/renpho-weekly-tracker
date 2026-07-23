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

## Design decisions

A record of the non-obvious calls made while building this, and why — mostly for my own future reference.

- **The `get_body_composition_measurements()` method doesn't exist.** Early planning assumed this method (referenced in some docs/examples) would sidestep a known truncation bug on BIA/smart scales. Reading the actual installed `v0.1.0` source (`renpho/client.py`) showed it was never shipped — only `get_all_measurements()` and the lower-level `get_measurements(table_name, user_id, total_count)` exist.
- **The truncation bug is real, but the fix is our own, not the library's.** `get_device_info()` reports a `count` per scale table, and `get_measurements()` stops paginating once it collects that many records. If Renpho's server under-reports `count`, real history gets silently cut off. Our fix: request `count + COUNT_PADDING` (200) instead of the exact reported count. This is safe because `get_measurements()` independently stops on the first empty page — overshooting the count is harmless, undershooting it isn't. See `fetcher.py`.
- **Idempotency key is Renpho's own `id` field, not `timeStamp`.** Verified by pulling one raw record and inspecting its keys (`scripts/exploration/inspect_fields.py`) — `id` is a distinct, stable field separate from `timeStamp` and the account's `bUserId`. Using it as the SQLite primary key with `INSERT OR REPLACE` means re-running the ingest never creates duplicate rows.
- **Zeroed bioimpedance fields mean "not measured," not "measured as zero."** Early records show `bodyfat`, `water`, `muscle`, etc. all at `0.0` while `weight`/`bmi` are real — this happens when a weigh-in doesn't register full foot contact with the scale's sensors. `store.py` stores the raw value exactly as returned (no cleaning at the storage layer); `analysis.py` is responsible for treating `0` as missing before averaging these fields.
- **`invalidFlag` looked like a built-in data-quality signal — it isn't.** Hypothesis tested empirically (`scripts/exploration/inspect_invalid_flag.py`) across all 1,553 records: `invalidFlag=3` (1,125 records) and `invalidFlag=0` (428 records) have statistically identical rates of zeroed bodyfat readings (0% vs 1%). Whatever the flag actually encodes, it isn't reading validity. Dropped from the schema; the outlier check below is our only data-quality safeguard.
- **Outlier safeguard: flag, don't discard.** `store.py`'s `OUTLIER_THRESHOLD_LB` (currently 10) flags any reading that differs from the prior one by more than that many pounds. Flagged rows stay in the database and appear in the report with a warning, but are excluded from weekly averages by default — since a big swing might be real (illness, dehydration) rather than a bad reading, and shouldn't be silently deleted either way.
- **One reading per calendar day (keep the latest).** A day with two weigh-ins (e.g. a re-check after a workout) would otherwise double-count toward that week's average. `analysis.py`'s `dedupe_to_daily()` collapses each day to its latest reading, capping weekly counts at 7. Done in `analysis.py`, not `store.py`, so SQLite keeps every raw reading.
- **Bucket by `timeStamp` (UTC epoch → America/New_York), NOT the stored `localCreatedAt`.** Initially indexed on `localCreatedAt` on the assumption it was the device's true local time (which would handle travel correctly). That was wrong: `localCreatedAt` is corrupted, running ~8 hours ahead of true UTC, which pushed afternoon weigh-ins into the next calendar day and misassigned them to the wrong week. Verified against the RENPHO app (which buckets by `timeStamp`) — converting the `timeStamp` epoch to the fixed home timezone (`America/New_York`, confirmed no cross-tz travel) reproduces the app's daily values and realistic morning/afternoon weigh-in times exactly. See `load_measurements()` in `analysis.py`.
- **Weeks run Sunday-Saturday**, via `pandas.resample('W-SAT')` rather than the pandas default (`'W'`, which ends weeks on Sunday). Matches how weigh-ins are actually tracked day-to-day, and `.mean()` over however many days were logged that week (5, 6, or 7) requires no extra handling.
- **Weight is stored/averaged in full precision, rounded only at display.** The scale's true value is kg (0.05 kg grid); the RENPHO app displays lb snapped to the nearest 0.2 lb (its lb-mode display resolution — which is why single readings like 174.94→175.0 and 175.49→175.4 look "inconsistently" rounded but aren't). We keep full-precision kg internally and round only in the display layer, since averaging pre-rounded values needlessly loses accuracy.
- **No session/token persistence.** `renpho-api` re-authenticates via `login()` on every run, with no exposed way to cache a session. Confirmed acceptable for a manual, once-a-week script.
- **The chart is inline SVG, not matplotlib.** The original plan allowed either. Loaded the project's dataviz design skill before building it and went with hand-built SVG: keeps `report.html` fully self-contained (no ~30MB matplotlib dependency), and SVG themes with light/dark mode, which a baked PNG can't.
- **The report renders client-side in JavaScript, not Python.** Started as pure Python string-rendering (like the table/chart logic still in `analysis.py`'s docstrings describe). Re-architected when an adjustable week-count control needed to survive a plain page reload — which never runs Python. `report.py` now embeds the *entire* weekly history as JSON in the page; a JS layer (a direct, comment-linked port of the original Python rendering functions, which were deleted rather than kept as an unsynced duplicate) renders the chart and table from it, driven by `localStorage`-backed controls (window size 2-12 weeks, ◀▶ history paging, table sort order). Only the window *size* persists across reload/regeneration — paging position always resets to "now."
- **Date/time formatting happens in Python, never in JS.** Every date string, week range, and the year(s)-spanned label the chart needs are precomputed server-side and embedded as plain strings/numbers. Given the `localCreatedAt` timezone bug earlier in this project, re-deriving dates client-side felt like reopening the same risk for no benefit — the browser only ever handles numbers and pre-formatted text.
- **Table dates match the chart's "Week of" framing.** Initially the table showed just the week-ending date; switched to a full span (`Jul 19 – Jul 25, 2026`) alongside the chart's range labels, since "Week of `<end-date>`" would misleadingly imply the week starts there.
- **Weekly-average precision vs. the scale's display precision.** The RENPHO app snaps individual daily readings to its 0.2 lb display grid; a weekly *average* is a computed trend signal that can meaningfully move by less than that. Daily values (in `analysis.py`'s `snap_to_scale_lb()`) round to match the scale; weekly averages/deltas in the report keep 1 decimal of real precision, so a real sub-0.2 lb weekly trend doesn't get rounded away to a false "no change."
- **Goal weight is tied to `GOAL_MODE`, not hardcoded to "down."** The in-progress week's target (1% off the prior completed week's average) flips direction automatically with the existing cut/bulk/neutral setting, rather than assuming a cut — stays correct without a second edit if the goal ever changes.

## Status

- [x] Phase 0 — spike, confirmed real data returns
- [x] Phase 1 — `fetcher.py` + `store.py`, idempotent SQLite ingest (verified: 1553 -> 1553 rows across two consecutive runs)
- [x] Phase 2 — weekly averages (`analysis.py`): Sun-Sat resample, week-over-week deltas, daily dedup, timezone-correct bucketing, verified against the RENPHO app
- [x] Phase 3 — interactive HTML report (`report.py`): inline SVG trend chart, sortable/goal-aware table, adjustable + persisted week window, light/dark theming
- [ ] Phase 4 — `run.py` entrypoint, error handling, polish
