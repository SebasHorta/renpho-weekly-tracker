"""One-off inspection script: figure out what invalidFlag actually signals.

Fetches your full history (reusing the padded-pagination approach from
spike.py) and tabulates invalidFlag values against whether bioimpedance
fields (bodyfat, water, etc.) came back zeroed. Safe to delete once Phase 1's
schema is settled.
"""

import os
from collections import Counter

from dotenv import load_dotenv
from renpho import RenphoClient
from renpho.export import format_timestamp


def fetch_all_raw_measurements(client: RenphoClient) -> list[dict]:
    """Same padded-pagination approach as spike.py -- pull full history across all scale tables."""
    device_info = client.get_device_info()
    scales = device_info.get("scale", [])

    all_measurements: list[dict] = []
    for scale in scales:
        table_name = scale.get("tableName")
        reported_count = scale.get("count", 0)
        user_ids = scale.get("userIds", [])

        if not table_name or reported_count == 0:
            continue

        uid = client.user_id
        if user_ids and uid not in user_ids:
            uid = user_ids[0]

        measurements = client.get_measurements(table_name, uid, reported_count + 200)
        all_measurements.extend(measurements)

    return all_measurements


def main():
    """Log in, pull full history, and report how invalidFlag correlates with zeroed bioimpedance data."""
    load_dotenv()
    client = RenphoClient(os.getenv("RENPHO_EMAIL"), os.getenv("RENPHO_PASSWORD"))
    client.login()

    measurements = fetch_all_raw_measurements(client)
    print(f"Total measurements: {len(measurements)}\n")

    flag_counts = Counter(m.get("invalidFlag") for m in measurements)
    print("invalidFlag value counts:")
    for value, count in sorted(flag_counts.items(), key=lambda kv: str(kv[0])):
        print(f"  {value!r}: {count}")

    # Does invalidFlag correlate with bodyfat coming back as 0 (i.e. no bioimpedance contact)?
    print("\nCross-check: invalidFlag vs. bodyfat==0 (zeroed bioimpedance reading):")
    for flag_value in sorted(flag_counts, key=lambda v: str(v)):
        subset = [m for m in measurements if m.get("invalidFlag") == flag_value]
        zero_bodyfat = sum(1 for m in subset if m.get("bodyfat") in (0, 0.0))
        print(f"  invalidFlag={flag_value!r}: {len(subset)} records, {zero_bodyfat} with bodyfat==0 ({zero_bodyfat / len(subset):.0%})")

    # Show a few sample records for any nonzero invalidFlag value, if they exist.
    nonzero = [m for m in measurements if m.get("invalidFlag") not in (0, None)]
    if nonzero:
        print(f"\nSample records with nonzero invalidFlag (showing up to 5 of {len(nonzero)}):")
        for m in nonzero[:5]:
            print(f"  time={format_timestamp(m.get('timeStamp'))} weight={m.get('weight')} "
                  f"bodyfat={m.get('bodyfat')} invalidFlag={m.get('invalidFlag')}")
    else:
        print("\nNo records with a nonzero invalidFlag were found in your history.")


if __name__ == "__main__":
    main()
