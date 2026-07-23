"""analysis.py -- turns raw SQLite measurements into weekly averages and week-over-week deltas.

Reads only from store.py's SQLite database, never talks to the Renpho API
directly -- keeps this module usable even if renpho-api breaks, and testable
against whatever history is already sitting in the database.
"""

import sqlite3

import pandas as pd

from .store import get_connection

# Metrics where a stored 0 means "not measured" (the scale didn't register
# full foot contact for bioimpedance), not "measured as zero" -- see the
# README's Design decisions. weight and bmi are excluded: both are always
# real whenever a row exists.
ZERO_MEANS_MISSING = [
    "bodyfat", "water", "muscle", "bone", "bmr", "visfat",
    "subfat", "protein", "bodyage", "sinew", "fatFreeWeight", "heartRate",
]

# All metrics we compute weekly averages (and deltas) for.
METRIC_COLUMNS = ["weight_lb", "bmi"] + ZERO_MEANS_MISSING

KG_PER_LB = 0.45359237

# The user's home timezone. Every reading is taken here (confirmed no
# cross-timezone travel), so we bucket all measurements by this single zone.
HOME_TZ = "America/New_York"

# The RENPHO scale/app displays weight in lb snapped to this resolution.
# Used only for displaying INDIVIDUAL daily readings, so the tool's number
# matches exactly what the scale showed. Weekly averages are NOT snapped to
# this -- averaging is a trend signal that can live below 0.2 lb, so those
# keep real precision (see the README's Design decisions).
SCALE_LB_RESOLUTION = 0.2


def snap_to_scale_lb(weight_lb: float) -> float:
    """Round a single reading to the scale's 0.2 lb display grid so it matches the RENPHO app.

    Display-only helper for individual daily readings (used by report.py in
    Phase 3). Never applied to weekly averages -- those keep full precision.
    """
    return round(weight_lb / SCALE_LB_RESOLUTION) * SCALE_LB_RESOLUTION


def load_measurements(conn: sqlite3.Connection) -> pd.DataFrame:
    """Load every stored measurement into a DataFrame indexed by local wall-clock time.

    Indexes on `timeStamp` (the absolute UTC epoch) converted to HOME_TZ,
    NOT on the stored `localCreatedAt` string. localCreatedAt was found to be
    corrupted -- running ~8 hours ahead of true UTC, which pushed afternoon
    weigh-ins into the next calendar day and misattributed them to the wrong
    week (verified against the RENPHO app, which buckets by timeStamp). The
    tz-aware epoch is converted to HOME_TZ and then made naive so it can be
    resampled as plain local wall-clock time. Zeroed bioimpedance fields are
    converted to NaN so later .mean() calls skip them automatically instead
    of treating "not measured" as "measured as zero."
    """
    df = pd.read_sql_query("SELECT * FROM measurements", conn)
    local_time = (
        pd.to_datetime(df["timeStamp"], unit="s", utc=True)
        .dt.tz_convert(HOME_TZ)
        .dt.tz_localize(None)
        .rename("local_time")
    )
    df = df.set_index(local_time).sort_index()

    for col in ZERO_MEANS_MISSING:
        df[col] = df[col].mask(df[col] == 0)

    df["weight_lb"] = df["weight"] / KG_PER_LB
    return df


def dedupe_to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse multiple same-day readings down to one per calendar day, keeping the latest.

    Caps weekly `readings` counts at 7 (one per day) -- a second weigh-in
    later the same day (e.g. checking again after a workout) shouldn't count
    as two data points toward that week's average. Relies on df already
    being sorted ascending by its datetime index (true coming out of
    load_measurements), so the last row within each calendar-day group is
    the chronologically latest reading for that day.
    """
    return df.groupby(df.index.normalize()).tail(1)


def weekly_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Resample to Sunday-Saturday weekly averages, excluding flagged outlier readings.

    Adds a `readings` column (how many logs contributed to that week's
    average -- 5, 6, or 7, no special-casing needed since .mean() just
    averages whatever rows fall in the window) and a `flagged_count` column
    (how many outlier readings were excluded that week, for visibility).
    Appends a `<metric>_delta` column for every metric, holding the
    week-over-week change from the prior week's average.
    """
    trusted = df[df["flagged"] == 0]

    weekly = trusted[METRIC_COLUMNS].resample("W-SAT").mean()
    weekly["readings"] = trusted["weight_lb"].resample("W-SAT").count()

    flagged_per_week = df[df["flagged"] == 1]["weight_lb"].resample("W-SAT").count()
    weekly["flagged_count"] = flagged_per_week.reindex(weekly.index).fillna(0).astype(int)

    weekly = weekly[weekly["readings"] > 0]

    for col in METRIC_COLUMNS:
        weekly[f"{col}_delta"] = weekly[col].diff()

    return weekly


if __name__ == "__main__":
    conn = get_connection()
    df = load_measurements(conn)
    df = dedupe_to_daily(df)
    weekly = weekly_summary(df)

    # Weekly averages/deltas keep 1 decimal of real precision (NOT snapped to
    # the scale's 0.2 lb grid) so small week-over-week trends stay visible.
    headline = weekly[[
        "readings", "flagged_count",
        "weight_lb", "weight_lb_delta",
        "bodyfat", "bodyfat_delta",
        "bmi", "bmi_delta",
    ]].round(1)

    print(headline.tail(12).to_string())
