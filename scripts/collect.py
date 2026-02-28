#!/usr/bin/env python3
"""
Collect SolarEdge production data and store it in the solaredge.production table.

Configuration via environment variables (put in .env and source before running,
or set directly in the cron environment):

    SOLAREDGE_API_KEY   — SolarEdge monitoring API key
    SOLAREDGE_SITE_ID   — Site ID
    DB_HOST             — Postgres host
    DB_PORT             — Postgres port (default: 5432)
    DB_NAME             — Database name
    DB_USER             — Database user
    DB_PASSWORD         — Database password
"""

import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import psycopg2
import psycopg2.extras

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

SOLAREDGE_BASE = "https://monitoringapi.solaredge.com"
SITE_ID = os.environ["SOLAREDGE_SITE_ID"]
API_KEY = os.environ.get("SOLAREDGE_API_KEY", "")

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def db_connect():
    return psycopg2.connect(**DB)


def latest_timestamp(conn) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(measured_at) FROM solaredge.production")
        row = cur.fetchone()
        return row[0] if row else None


def fetch_production(start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    fmt = "%Y-%m-%d %H:%M:%S"
    params = {
        "api_key": API_KEY,
        "startTime": start.astimezone(AMSTERDAM).strftime(fmt),
        "endTime": end.astimezone(AMSTERDAM).strftime(fmt),
    }
    url = f"{SOLAREDGE_BASE}/site/{SITE_ID}/powerDetails"
    resp = httpx.get(url, params=params, timeout=30)
    resp.raise_for_status()

    meters = resp.json()["powerDetails"]["meters"]
    production = next((m for m in meters if m["type"] == "Production"), None)
    if not production:
        return []

    results = []
    for entry in production["values"]:
        if entry.get("value") is None:
            continue
        # Parse as Amsterdam local time, convert to UTC-aware datetime
        local_dt = datetime.strptime(entry["date"], fmt).replace(tzinfo=AMSTERDAM)
        utc_dt = local_dt.astimezone(UTC)
        results.append((utc_dt, float(entry["value"])))
    return results


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
    if not API_KEY:
        log.error("SOLAREDGE_API_KEY is not set")
        sys.exit(1)

    now = datetime.now(UTC)

    with db_connect() as conn:
        latest = latest_timestamp(conn)
        if latest is None:
            # First run: fetch the last 7 days
            start = now - timedelta(days=7)
            log.info("No existing data; backfilling from %s", start.date())
        else:
            # Start from the last recorded interval to catch any updates
            start = latest - timedelta(minutes=15)
            log.info("Fetching from %s", start)

        rows = fetch_production(start=start, end=now)
        if not rows:
            log.info("No new production data returned")
            return

        upsert(conn, rows)
        log.info("Upserted %d rows (latest: %s)", len(rows), max(r[0] for r in rows))


if __name__ == "__main__":
    main()
