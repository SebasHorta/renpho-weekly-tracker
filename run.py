"""run.py -- the single command to run: fetch -> store -> report -> open.

This is the project's actual entry point (everything under renpho_tool/ --
fetcher.py, store.py, analysis.py, report.py -- is a library module that
this script chains together). Run with: venv/bin/python run.py
"""

import webbrowser
from pathlib import Path

from renpho_tool.fetcher import fetch_measurements
from renpho_tool.report import write_report
from renpho_tool.store import get_connection, upsert_measurements


def main():
    """Sync fresh data from Renpho if possible, then always regenerate and open the report.

    The fetch step is wrapped in a deliberately broad except: renpho-api is
    an unofficial, reverse-engineered client that "can break without warning"
    (see the README) -- bad login, dropped connection, Renpho changing their
    backend, whatever. SQLite is the project's whole reason for existing as a
    source of truth, so any failure here should fall back to reporting on
    whatever's already synced, not crash. A narrower except (just
    RenphoAPIError) would miss plain connection errors and defeat that point.
    """
    try:
        measurements = fetch_measurements()
        conn = get_connection()
        total = upsert_measurements(conn, measurements)
        print(f"Synced with Renpho. {total} rows in the database.")
    except Exception as e:
        print(f"Couldn't sync with Renpho ({e}). Falling back to existing local data.")

    path = write_report()
    print(f"Report generated: {path}")
    # webbrowser.open() expects a URL, not a bare relative path -- a real
    # file:// URI opens reliably across browsers/OSes, a relative path doesn't.
    webbrowser.open(Path(path).resolve().as_uri())


if __name__ == "__main__":
    main()
