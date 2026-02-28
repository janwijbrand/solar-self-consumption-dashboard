#!/usr/bin/env python3
"""
Calculate electricity consumed from the grid for a given time interval.
Timestamps are interpreted as Europe/Amsterdam local time.

Usage:
    python energy.py "2026-02-27 09:30" "2026-02-27 11:00"
"""

import os
import sys
import zoneinfo
from datetime import datetime

import psycopg2

DB = {
    "host": os.environ["DB_HOST"],
    "port": int(os.environ.get("DB_PORT", 5432)),
    "dbname": os.environ["DB_NAME"],
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}

TZ = zoneinfo.ZoneInfo("Europe/Amsterdam")
FMT = "%Y-%m-%d %H:%M"

QUERY = """
SELECT
    (end_r.delivered_1 - start_r.delivered_1)
  + (end_r.delivered_2 - start_r.delivered_2) AS consumed_kwh,
  start_r.read_at AS actual_start,
  end_r.read_at   AS actual_end
FROM
    (SELECT delivered_1, delivered_2, read_at
     FROM dsmr_consumption_electricityconsumption
     WHERE read_at >= %s
     ORDER BY read_at ASC
     LIMIT 1) AS start_r,
    (SELECT delivered_1, delivered_2, read_at
     FROM dsmr_consumption_electricityconsumption
     WHERE read_at <= %s
     ORDER BY read_at DESC
     LIMIT 1) AS end_r;
"""


def parse_local(ts: str) -> datetime:
    return datetime.strptime(ts, FMT).replace(tzinfo=TZ)


def main():
    if len(sys.argv) != 3:
        print(f'Usage: {sys.argv[0]} "YYYY-MM-DD HH:MM" "YYYY-MM-DD HH:MM"')
        sys.exit(1)

    start = parse_local(sys.argv[1])
    end = parse_local(sys.argv[2])

    if end <= start:
        print("Error: end time must be after start time.")
        sys.exit(1)

    with psycopg2.connect(**DB) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY, (start, end))
            row = cur.fetchone()

    if row is None or row[0] is None:
        print("No data found for the given interval.")
        sys.exit(1)

    consumed_kwh, actual_start, actual_end = row
    duration = actual_end - actual_start

    print(f"Interval : {actual_start.strftime(FMT)} → {actual_end.strftime(FMT)}")
    print(f"Duration : {duration}")
    print(f"Consumed : {consumed_kwh:.3f} kWh")


if __name__ == "__main__":
    main()
