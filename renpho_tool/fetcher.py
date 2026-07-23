"""fetcher.py -- pulls raw measurements from the Renpho cloud API and normalizes them.

Owns everything that talks to renpho-api directly. store.py never imports the
renpho package -- it only receives the plain dicts this module returns, so if
renpho-api ever breaks or gets replaced (e.g. with a CSV import instead), only
this file needs to change.
"""

import os

from dotenv import load_dotenv
from renpho import RenphoClient

# Padding added to Renpho's reported per-table record count before requesting
# it -- see spike.py for the original investigation. Renpho's count can
# under-report on BIA/smart scales; get_measurements() independently stops on
# the first empty page, so asking for "too many" is harmless while trusting
# an under-reported count silently truncates real history.
COUNT_PADDING = 200

# Fields we keep from Renpho's raw record, matching store.py's schema exactly.
# Everything else in the raw response (app/device plumbing like 'mac',
# 'appVersion', 'babyPicture', etc.) is discarded here and never leaves this file.
KEPT_FIELDS = [
    "id", "timeStamp", "localCreatedAt", "weight", "bmi", "bodyfat", "water",
    "muscle", "bone", "bmr", "visfat", "subfat", "protein", "bodyage",
    "sinew", "fatFreeWeight", "heartRate", "scaleName",
]


def _normalize(raw: dict) -> dict:
    """Trim one raw Renpho record down to just the fields store.py's schema expects."""
    return {field: raw.get(field) for field in KEPT_FIELDS}


def _fetch_raw_measurements(client: RenphoClient) -> list[dict]:
    """Pull every measurement across all scale tables tied to this account, with padded pagination."""
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

        padded_count = reported_count + COUNT_PADDING
        records = client.get_measurements(table_name, uid, padded_count)

        if len(records) >= padded_count:
            print(
                f"WARNING: table={table_name} returned {len(records)} records, "
                f"meeting or exceeding the padded request of {padded_count}. "
                f"COUNT_PADDING may be exhausted -- consider raising it."
            )

        all_measurements.extend(records)

    return all_measurements


def fetch_measurements() -> list[dict]:
    """Log in with credentials from .env and return your full measurement history, normalized.

    This is the one function the rest of the pipeline calls -- it hides
    login, pagination, and field-trimming behind a single call.
    """
    load_dotenv()
    client = RenphoClient(os.getenv("RENPHO_EMAIL"), os.getenv("RENPHO_PASSWORD"))
    client.login()

    raw = _fetch_raw_measurements(client)
    return [_normalize(r) for r in raw]


if __name__ == "__main__":
    measurements = fetch_measurements()
    print(f"Fetched {len(measurements)} measurements.")
