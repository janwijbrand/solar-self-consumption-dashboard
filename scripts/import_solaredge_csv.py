#!/usr/bin/env python3
"""
One-off import of SolarEdge CSV export into solaredge.production.

Usage:
    python scripts/import_solaredge_csv.py path/to/file.csv

CSV format expected (Amsterdam local time, AM/PM):
    Time,Inv1 AC Production - Power (W)
    "1/31/26, 12:00 AM",
    "1/31/26, 10:00 AM",122.042564
    ...

Empty power cells are skipped (consistent with collect.py).
Uses the same DB env vars as collect.py.
"""

import csv
import os
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

AMSTERDAM = ZoneInfo("Europe/Amsterdam")

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)

TIME_FMT = "%m/%d/%y, %I:%M %p"


def parse_csv(path: str) -> list[tuple[datetime, float]]:
    rows = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for line in reader:
            if len(line) < 2 or not line[1].strip():
                continue
            local_dt = datetime.strptime(line[0], TIME_FMT).replace(tzinfo=AMSTERDAM)
            utc_dt = local_dt.astimezone(UTC)
            rows.append((utc_dt, float(line[1])))
    return rows


def upsert(conn, rows: list[tuple[datetime, float]]):
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO solaredge.production (measured_at, power_w)
            VALUES %s
            ON CONFLICT (measured_at) DO UPDATE SET power_w = EXCLUDED.power_w
            """,
            rows,
        )
    conn.commit()


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <csv_file>", file=sys.stderr)
        sys.exit(1)

    rows = parse_csv(sys.argv[1])
    print(f"Parsed {len(rows)} rows from CSV")

    with psycopg2.connect(**DB) as conn:
        upsert(conn, rows)
    print(f"Upserted {len(rows)} rows into solaredge.production")


if __name__ == "__main__":
    main()
