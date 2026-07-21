"""Phase 0 spike: confirm renpho-api returns real data before building the pipeline.

Not part of the final tool -- fetcher.py/store.py will replace this once the
approach is verified. Run with: venv/bin/python spike.py
"""

import os

from dotenv import load_dotenv
from renpho import RenphoClient
from renpho.export import format_timestamp


def fetch_all_raw_measurements(client: RenphoClient) -> list[dict]:
    """Pull every measurement across all scale tables registered to this account.

    Calls get_device_info() to discover each scale's table name and reported
    record count, then paginates through get_measurements() per table. We pad
    the reported count before passing it in: Renpho's count field is known to
    under-report for BIA/smart scales, but get_measurements() independently
    stops on the first empty page, so overshooting the count is harmless while
    trusting an under-reported count silently truncates real history.
    """
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

        padded_count = reported_count + 200
        print(f"  table={table_name} reported_count={reported_count} (padded request to {padded_count})")
        measurements = client.get_measurements(table_name, uid, padded_count)
        all_measurements.extend(measurements)

    return all_measurements


def main():
    """Log in, pull raw measurements, and print a summary to eyeball against real weigh-in history."""
    load_dotenv()
    email = os.getenv("RENPHO_EMAIL")
    password = os.getenv("RENPHO_PASSWORD")

    client = RenphoClient(email, password)
    print(f"Logging in as {email}...")
    client.login()
    print(f"Logged in. user_id={client.user_id}")

    measurements = fetch_all_raw_measurements(client)
    measurements.sort(key=lambda m: m.get("timeStamp", 0) or 0)

    print(f"\nTotal measurements returned: {len(measurements)}")
    if measurements:
        first, last = measurements[0], measurements[-1]
        print(f"Earliest: {format_timestamp(first.get('timeStamp'))}  weight={first.get('weight')} kg")
        print(f"Latest:   {format_timestamp(last.get('timeStamp'))}  weight={last.get('weight')} kg")


if __name__ == "__main__":
    main()
