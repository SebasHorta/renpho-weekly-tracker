"""One-off inspection script: print the raw field names of a single measurement.

Not part of the pipeline -- used only to confirm real field names (and find a
stable unique key) before designing the SQLite schema in store.py. Safe to
delete once Phase 1's schema is settled.
"""

import json
import os

from dotenv import load_dotenv
from renpho import RenphoClient


def main():
    """Log in, fetch one page of measurements, and print one record's raw structure."""
    load_dotenv()
    client = RenphoClient(os.getenv("RENPHO_EMAIL"), os.getenv("RENPHO_PASSWORD"))
    client.login()

    device_info = client.get_device_info()
    scale = device_info["scale"][0]
    table_name = scale["tableName"]
    uid = client.user_id

    # total_count=1 still returns one full page (page_size defaults to 50) --
    # we only need to see the field shape, not the full history, for this check.
    measurements = client.get_measurements(table_name, uid, total_count=1)

    sample = measurements[0]
    print(f"Fetched {len(measurements)} record(s) in this page.\n")
    print("Keys present in one raw measurement record:")
    for key in sorted(sample.keys()):
        print(f"  {key!r}: {sample[key]!r}")

    print("\nFull record as JSON:")
    print(json.dumps(sample, indent=2, default=str))


if __name__ == "__main__":
    main()
