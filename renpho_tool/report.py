"""report.py -- renders the weekly summary into a self-contained report.html.

Reads only from the weekly summary produced by analysis.py (which in turn
reads only from SQLite), so this never touches the Renpho API. The HTML
shell, CSS, and JS that make up the page live as real source files under
report_assets/ (not embedded as Python strings) -- this module's job is just
computing the data, then stitching those source files together with it into
one self-contained report.html. Output is still a single double-clickable
file with no external file dependencies -- report_assets/ is a build-time
input, not something the generated report.html links to at runtime.
"""

import json
from pathlib import Path

import pandas as pd

from .analysis import (
    HOME_TZ,
    dedupe_to_daily,
    load_measurements,
    snap_to_scale_lb,
    weekly_summary,
)
from .store import get_connection

# report_assets/ lives at the project root, one level up from this package
# (renpho_tool/report.py -> renpho_tool/ -> project root -> report_assets/).
ASSETS_DIR = Path(__file__).resolve().parent.parent / "report_assets"

# How your current training goal colors the weekly weight/BMI deltas. Flip this
# 2-3x/year as you switch phases:
#   "cut"     -> weight/BMI DOWN is green (good), UP is red
#   "bulk"    -> weight/BMI UP is green (good), DOWN is red
#   "neutral" -> no good/bad judgment; deltas shown in a muted neutral color
# Body fat % ignores this and always treats DOWN as good.
GOAL_MODE = "cut"

# Fixed baseline for the "target trend" line and the table's "vs Target"
# column: the week-ending date (YYYY-MM-DD, a Saturday) whose actual average
# becomes the starting point that 1%/week compounds forward from. Set this to
# whenever your CURRENT cut/bulk actually started -- re-set it each time you
# start a new phase, same cadence as GOAL_MODE. Leave as None to fall back to
# WEEKS_SHOWN weeks back from your most recent completed week, which exists
# only so the feature shows something before you've set a real start date.
GOAL_ANCHOR_WEEK_ENDING: str | None = None  # e.g. "2026-05-02"

# Default number of most-recent weeks shown in the chart and table. The report
# page lets you change this at runtime (a number input, persisted in the
# browser's localStorage) -- this constant is only the fallback used the very
# first time the page is opened, before any preference has been saved.
WEEKS_SHOWN = 12
MIN_WEEKS_SELECTABLE = 1
MAX_WEEKS_SELECTABLE = 12

# Palette (from the dataviz skill's validated reference palette). Delta cues use
# the status green/red, always paired with an arrow so meaning never relies on
# color alone.
COLOR_GOOD = "#0ca30c"
COLOR_BAD = "#d03b3b"


def _is_in_progress(week_ending: pd.Timestamp) -> bool:
    """True if this week's Saturday end-date is today or later, i.e. the week hasn't finished being logged."""
    today = pd.Timestamp.now(tz=HOME_TZ).date()
    return week_ending.date() >= today


def _json_safe(value):
    """Convert a pandas/numpy scalar to something json.dumps can serialize, mapping NaN to None."""
    if pd.isna(value):
        return None
    return float(value) if isinstance(value, float) else int(value)


def _weekly_to_records(weekly: pd.DataFrame) -> list[dict]:
    """Serialize the FULL weekly history (not just a recent slice) to plain dicts for embedding as JSON.

    The report page needs every week available client-side so the "weeks
    shown" control can re-slice without re-running Python. Date formatting
    (week range for the chart, full label for the table) and the in-progress
    flag are precomputed here rather than in JS, so the browser never has to
    parse or reason about dates/timezones -- just strings and numbers.
    """
    records = []
    for week_ending, r in weekly.iterrows():
        week_start = week_ending - pd.Timedelta(days=6)
        records.append({
            # Plain, unambiguous string key for matching a specific week
            # client-side (e.g. finding the goal-trend anchor) -- comparing
            # this by equality avoids ever parsing a date back out of a
            # display string in JS.
            "week_ending_iso": week_ending.strftime("%Y-%m-%d"),
            "week_range_short": f"{week_start.strftime('%-m/%-d')}-{week_ending.strftime('%-m/%-d')}",
            # Used to build the chart's "Week of <year>" axis title -- kept as
            # a plain int so the JS side never has to parse it back out of a
            # formatted string.
            "year": week_ending.year,
            # Full week span for the table -- paired with the "Week of" header,
            # same reasoning as the chart's x-axis: a single end-date next to
            # "Week of" would misleadingly imply the week starts there.
            "week_label": f"{week_start.strftime('%b %-d')} – {week_ending.strftime('%b %-d, %Y')}",
            "in_progress": _is_in_progress(week_ending),
            "readings": int(r["readings"]),
            "flagged_count": int(r["flagged_count"]),
            "weight_lb": _json_safe(r["weight_lb"]),
            "weight_lb_delta": _json_safe(r["weight_lb_delta"]),
            "bodyfat": _json_safe(r["bodyfat"]),
            "bodyfat_delta": _json_safe(r["bodyfat_delta"]),
            "bmi": _json_safe(r["bmi"]),
            "bmi_delta": _json_safe(r["bmi_delta"]),
        })
    return records


def _daily_to_records(daily: pd.DataFrame) -> list[dict]:
    """Serialize every deduped daily reading to plain dicts, for the single-week chart view.

    weight_lb is snapped to the scale's 0.2 lb display grid (snap_to_scale_lb)
    rather than kept at full precision -- these are individual readings, not
    an average, so they should read exactly like what the scale showed (see
    the README's Design decisions on daily-vs-weekly precision). week_ending_iso
    links each day back to the Sun-Sat week it belongs to, computed the same
    way pandas' 'W-SAT' resample groups days, so the JS side can filter to
    "just this week's days" with a plain string match, no date math needed.
    """
    records = []
    for day, r in daily.iterrows():
        week_ending = day + pd.Timedelta(days=(5 - day.dayofweek) % 7)
        records.append({
            "date_iso": day.strftime("%Y-%m-%d"),
            "day_label": day.strftime("%a %-m/%-d"),
            "week_ending_iso": week_ending.strftime("%Y-%m-%d"),
            "weight_lb": _json_safe(snap_to_scale_lb(r["weight_lb"])) if pd.notna(r["weight_lb"]) else None,
        })
    return records


def _resolve_goal_anchor(weekly: pd.DataFrame) -> str | None:
    """Return the ISO week-ending date the goal-trend line should start from.

    Uses GOAL_ANCHOR_WEEK_ENDING if set; otherwise falls back to WEEKS_SHOWN
    weeks back from the most recent COMPLETED week (excluding the in-progress
    one, which has no full average yet), purely so the trend line/deviation
    column show something before a real start date is configured. Returns
    None if GOAL_MODE is "neutral" (no direction to project) or there's no
    completed history yet.
    """
    if GOAL_MODE == "neutral":
        return None
    if GOAL_ANCHOR_WEEK_ENDING is not None:
        return GOAL_ANCHOR_WEEK_ENDING

    completed = weekly[~weekly.index.map(_is_in_progress)]
    if completed.empty:
        return None
    anchor_pos = max(0, len(completed) - WEEKS_SHOWN)
    return completed.index[anchor_pos].strftime("%Y-%m-%d")


def render_report(weekly: pd.DataFrame, daily: pd.DataFrame) -> str:
    """Assemble the full self-contained HTML document by stitching report_assets/ source files together with this run's data.

    Reads template.html (the page shell), styles.css, and report.js from
    report_assets/, then substitutes each __RENPHO_*__ marker in the template
    with either static content (the CSS/JS text) or per-run values (the
    embedded weekly/daily JSON, the goal config, today's date). Plain string
    .replace() rather than str.format(): the CSS/JS being inlined is full of
    literal { } characters, which .format() would misread as its own
    placeholders. Called by write_report.
    """
    generated = pd.Timestamp.now(tz=HOME_TZ).strftime("%b %-d, %Y %-I:%M %p")
    goal_anchor_week_ending = _resolve_goal_anchor(weekly)
    goal_label = {"cut": "Cutting", "bulk": "Bulking", "neutral": "Maintaining"}[GOAL_MODE]

    # The only bridge between Python config and the static report.js file --
    # see report_assets/report.js's `const CONFIG = window.__RENPHO_CONFIG__`.
    config = {
        "goalMode": GOAL_MODE,
        "colorGood": COLOR_GOOD,
        "colorBad": COLOR_BAD,
        "defaultWeeks": WEEKS_SHOWN,
        "minWeeks": MIN_WEEKS_SELECTABLE,
        "maxWeeks": MAX_WEEKS_SELECTABLE,
        "defaultAnchorIso": goal_anchor_week_ending,
    }

    template = (ASSETS_DIR / "template.html").read_text()
    styles = (ASSETS_DIR / "styles.css").read_text()
    report_js = (ASSETS_DIR / "report.js").read_text()

    replacements = {
        "__RENPHO_STYLES__": styles,
        "__RENPHO_GENERATED__": generated,
        "__RENPHO_GOAL_LABEL__": goal_label,
        "__RENPHO_MIN_WEEKS__": str(MIN_WEEKS_SELECTABLE),
        "__RENPHO_MAX_WEEKS__": str(MAX_WEEKS_SELECTABLE),
        "__RENPHO_WEEKS_SHOWN__": str(WEEKS_SHOWN),
        "__RENPHO_WEEKLY_DATA__": json.dumps(_weekly_to_records(weekly)),
        "__RENPHO_DAILY_DATA__": json.dumps(_daily_to_records(daily)),
        # Named distinctly from the literal "window.__RENPHO_CONFIG__" text
        # in template.html/report.js -- using the same token for both would
        # have this replace() corrupt the JS global's own name (it did, the
        # first time; caught by grepping the actual output for stray markers).
        "__RENPHO_CONFIG_JSON__": json.dumps(config),
        "__RENPHO_REPORT_JS__": report_js,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def write_report(path: str = "report.html") -> str:
    """Load history, compute the weekly summary, render the HTML, and write it to `path`.

    The single entry point for this module (called by run.py in Phase 4, and
    by this file's __main__). Returns the path written.
    """
    conn = get_connection()
    daily = dedupe_to_daily(load_measurements(conn))
    weekly = weekly_summary(daily)
    with open(path, "w") as f:
        f.write(render_report(weekly, daily))
    return path


if __name__ == "__main__":
    out = write_report()
    print(f"Wrote {out}")
