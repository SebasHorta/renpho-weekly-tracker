"""store.py -- SQLite persistence layer, and the project's source of truth.

Once a measurement lands here, the tool keeps working even if renpho-api
breaks or Renpho changes their backend -- analysis.py and report.py only ever
read from this file, never from the live API. This module never imports
renpho; it only accepts the plain dicts fetcher.py produces.
"""

import sqlite3

DB_PATH = "renpho_data.db"

# Outlier threshold, in pounds. A new reading that differs from the prior one
# by more than this gets flagged=1 (excluded from weekly averages by default,
# but kept in the DB and shown in the report with a warning) instead of being
# silently trusted or discarded. Edit this single number to change sensitivity.
OUTLIER_THRESHOLD_LB = 10

KG_PER_LB = 0.45359237


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite file at db_path and ensure the schema exists."""
    conn = sqlite3.connect(db_path)
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create the measurements table if it doesn't already exist. Safe to call every run."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS measurements (
            id             INTEGER PRIMARY KEY,
            timeStamp      INTEGER NOT NULL,
            localCreatedAt TEXT,
            weight         REAL,
            bmi            REAL,
            bodyfat        REAL,
            water          REAL,
            muscle         REAL,
            bone           REAL,
            bmr            REAL,
            visfat         REAL,
            subfat         REAL,
            protein        REAL,
            bodyage        REAL,
            sinew          REAL,
            fatFreeWeight  REAL,
            heartRate      REAL,
            scaleName      TEXT,
            flagged        INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()


def _most_recent_weight_kg(conn: sqlite3.Connection) -> float | None:
    """Return the weight (kg) of whichever stored row has the latest timeStamp, or None if the table's empty."""
    row = conn.execute(
        "SELECT weight FROM measurements ORDER BY timeStamp DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def upsert_measurements(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert new measurements, overwriting any existing row with the same id, flagging outliers as we go.

    Processes rows oldest-to-newest and compares each to a rolling "previous
    weight" reference (seeded from whatever's already the most recent row in
    the DB) so a jump bigger than OUTLIER_THRESHOLD_LB gets flagged=1 instead
    of silently skewing weekly averages later. Keyed on Renpho's own `id`
    field (confirmed unique via inspect_fields.py), so re-running this on the
    same data never creates duplicate rows -- INSERT OR REPLACE just
    overwrites each row with the same values, satisfying idempotency.

    Returns the total row count in the table after the upsert.
    """
    rows_sorted = sorted(rows, key=lambda r: r["timeStamp"])
    previous_weight_kg = _most_recent_weight_kg(conn)

    for row in rows_sorted:
        weight_kg = row.get("weight")
        flagged = 0
        if previous_weight_kg is not None and weight_kg is not None:
            delta_lb = abs(weight_kg - previous_weight_kg) / KG_PER_LB
            flagged = 1 if delta_lb > OUTLIER_THRESHOLD_LB else 0
        if weight_kg is not None:
            previous_weight_kg = weight_kg

        conn.execute("""
            INSERT OR REPLACE INTO measurements (
                id, timeStamp, localCreatedAt, weight, bmi, bodyfat, water,
                muscle, bone, bmr, visfat, subfat, protein, bodyage,
                sinew, fatFreeWeight, heartRate, scaleName, flagged
            ) VALUES (
                :id, :timeStamp, :localCreatedAt, :weight, :bmi, :bodyfat, :water,
                :muscle, :bone, :bmr, :visfat, :subfat, :protein, :bodyage,
                :sinew, :fatFreeWeight, :heartRate, :scaleName, :flagged
            )
        """, {**row, "flagged": flagged})

    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
