#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Jan-Wijbrand Kolman
# SPDX-License-Identifier: MIT
"""
Collect SolarEdge production data and store it in the solaredge.production table.

Uses the SolarEdge Monitoring API V2 (V1 was retired on 2026-11-03). Run
scripts/solaredge_auth.py once to store an OAuth grant; token refresh is automatic.

Cron runs this every 20 minutes, but it only calls the API while the sun is up.
The free V2 tier allows 2,000 calls per month, and polling every 15 minutes around
the clock would need about 2,900. Each call returns every 15-minute value since the
last stored row, so the stored resolution is unchanged and night-time gaps fill
themselves on the first run in the morning.

Usage:
    python scripts/collect.py            # normal cron run
    python scripts/collect.py --force    # skip the daylight check

Configuration via environment variables (see also scripts/solaredge.py):

    SITE_LAT, SITE_LON  — site location, for the daylight check
    DB_HOST             — Postgres host
    DB_PORT             — Postgres port (default: 5432)
    DB_NAME             — Database name
    DB_USER             — Database user
    DB_PASSWORD         — Database password
"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta

import pandas as pd
import psycopg2
import psycopg2.extras
import solaredge
from pvlib.solarposition import get_solarposition

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

SITE_LAT = float(os.environ["SITE_LAT"])
SITE_LON = float(os.environ["SITE_LON"])

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)

FIRST_RUN_DAYS = 7


def db_connect():
    return psycopg2.connect(**DB)


def sun_is_up(now: datetime) -> bool:
    pos = get_solarposition(pd.DatetimeIndex([now]), SITE_LAT, SITE_LON)
    return float(pos["apparent_elevation"].iloc[0]) > 0.0


def latest_timestamp(conn) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(measured_at) FROM solaredge.production")
        row = cur.fetchone()
        return row[0] if row else None


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
    parser = argparse.ArgumentParser(description="Collect SolarEdge production data.")
    parser.add_argument("--force", action="store_true", help="run even when the sun is down")
    args = parser.parse_args()

    now = datetime.now(UTC)
    if not args.force and not sun_is_up(now):
        log.info("SolarEdge: sun is down, skipping")
        return

    with db_connect() as conn:
        latest = latest_timestamp(conn)
        if latest is None:
            start = now - timedelta(days=FIRST_RUN_DAYS)
            log.info("No existing data; backfilling from %s", start.date())
        else:
            # Start from the last recorded interval to catch any updates
            start = latest - timedelta(minutes=15)
            log.info("Fetching from %s", start)

        total = 0
        newest = None
        try:
            # Upsert per call, so a failure part-way through a long gap keeps progress.
            for rows in solaredge.fetch_power(conn, start, now):
                if rows:
                    upsert(conn, rows)
                    total += len(rows)
                    newest = max(r[0] for r in rows)
        except solaredge.SolarEdgeError as e:
            log.error("SolarEdge: %s (stored %d rows before the error)", e, total)
            sys.exit(1)

        if total:
            log.info("Upserted %d rows (latest: %s)", total, newest)
        else:
            log.info("No new production data returned")


if __name__ == "__main__":
    main()
