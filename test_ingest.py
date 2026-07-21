"""Phase 1 test driver: run fetcher.py -> store.py once and print the resulting row count.

Not the final entry point -- run.py (Phase 4) will replace this with proper
error handling. This just exists to verify the idempotency requirement: run
it twice back-to-back and confirm the row count doesn't change the second time.
"""

from fetcher import fetch_measurements
from store import get_connection, upsert_measurements


def main():
    """Fetch current Renpho history and upsert it into SQLite, then print the total row count."""
    measurements = fetch_measurements()
    print(f"Fetched {len(measurements)} measurements from Renpho.")

    conn = get_connection()
    total_rows = upsert_measurements(conn, measurements)
    print(f"Total rows in {conn.execute('PRAGMA database_list').fetchone()[2]}: {total_rows}")


if __name__ == "__main__":
    main()
