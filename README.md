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
venv/bin/pip install "renpho-api[dotenv]"
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
- **Weeks run Sunday-Saturday**, via `pandas.resample('W-SAT')` rather than the pandas default (`'W'`, which ends weeks on Sunday). Matches how weigh-ins are actually tracked day-to-day, and `.mean()` over however many days were logged that week (5, 6, or 7) requires no extra handling.
- **No session/token persistence.** `renpho-api` re-authenticates via `login()` on every run, with no exposed way to cache a session. Confirmed acceptable for a manual, once-a-week script.

## Status

- [x] Phase 0 — spike, confirmed real data returns
- [x] Phase 1 — `fetcher.py` + `store.py`, idempotent SQLite ingest (verified: 1553 -> 1553 rows across two consecutive runs)
- [ ] Phase 2 — weekly averages (`analysis.py`)
- [ ] Phase 3 — HTML report (`report.py`)
- [ ] Phase 4 — `run.py` entrypoint, error handling, polish
