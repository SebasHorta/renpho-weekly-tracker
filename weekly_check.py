"""weekly_check.py -- headless automated entry point: sync, then email last week's summary once.

Designed to be run on a schedule (daily, via launchd -- see
com.renpho.weeklycheck.plist). Unlike run.py it opens no browser and prints
only terse log lines (launchd captures stdout/stderr to a log file). The
once-per-week guard in store.py means running this every day still sends
exactly one email per completed Sun-Sat week, the first day that week is done.

Run manually with: venv/bin/python weekly_check.py
"""

from datetime import datetime

from analysis import dedupe_to_daily, load_measurements, weekly_summary
from fetcher import fetch_measurements
from notify import build_email, send_email, target_week_ending
from store import get_connection, has_notified, mark_notified, upsert_measurements


def log(message: str) -> None:
    """Print a timestamped line so the launchd log shows when each run happened and what it did."""
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {message}", flush=True)


def main():
    """Sync fresh data if possible, then email the most recent completed week's summary if unsent.

    The fetch is wrapped in the same deliberately broad except as run.py: an
    unofficial API that can break shouldn't stop us from reporting on data we
    already have. Email send failures deliberately do NOT mark the week as
    notified, so a transient failure (network, Gmail hiccup) just retries on
    tomorrow's run instead of silently dropping that week's summary.
    """
    conn = get_connection()

    try:
        measurements = fetch_measurements()
        total = upsert_measurements(conn, measurements)
        log(f"Synced with Renpho. {total} rows in the database.")
    except Exception as e:
        log(f"Couldn't sync with Renpho ({e}). Proceeding with existing local data.")

    weekly = weekly_summary(dedupe_to_daily(load_measurements(conn)))
    week_ending = target_week_ending(weekly)

    if week_ending is None:
        log("No completed week to summarize yet. Nothing to send.")
        return

    if has_notified(conn, week_ending):
        log(f"Week ending {week_ending} already emailed. Nothing to send.")
        return

    subject, html_body = build_email(weekly, week_ending)
    send_email(subject, html_body)  # raises on failure -> week stays unsent, retried next run
    mark_notified(conn, week_ending)
    log(f"Emailed summary for week ending {week_ending}: {subject!r}")


if __name__ == "__main__":
    main()
