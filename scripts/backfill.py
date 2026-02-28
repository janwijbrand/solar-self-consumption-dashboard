#!/usr/bin/env python3
"""
One-time (resumable) backfill of SolarEdge production data.

Fetches month by month from START_DATE to today. Already-populated months
are skipped, so the script can be safely re-run if interrupted or rate-limited.

SolarEdge allows max ~1 month per powerDetails request and 300 requests/day.
~79 months (Jul 2019 → now) = 79 requests — comfortably within the daily quota.

Usage:
    source .env
    .venv/bin/python backfill.py
"""

import logging
import os
import sys
import time
from calendar import monthrange
from datetime import UTC, datetime, timedelta, timezone

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

AMSTERDAM = timezone(timedelta(hours=1))
START_DATE = datetime(2019, 7, 1, tzinfo=AMSTERDAM)
REQUEST_DELAY = 1.0  # seconds between API calls


def db_connect():
    return psycopg2.connect(**DB)


def month_has_data(conn, year: int, month: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM solaredge.production
            WHERE measured_at >= %s AND measured_at < %s
            LIMIT 1
            """,
            (
                datetime(year, month, 1, tzinfo=UTC),
                datetime(year, month + 1 if month < 12 else 1, 1, tzinfo=UTC)
                if month < 12
                else datetime(year + 1, 1, 1, tzinfo=UTC),
            ),
        )
        return cur.fetchone() is not None


def fetch_month(year: int, month: int) -> list[tuple[datetime, float]]:
    last_day = monthrange(year, month)[1]
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=AMSTERDAM)
    end = datetime(year, month, last_day, 23, 59, 59, tzinfo=AMSTERDAM)
    fmt = "%Y-%m-%d %H:%M:%S"

    resp = httpx.get(
        f"{SOLAREDGE_BASE}/site/{SITE_ID}/powerDetails",
        params={
            "api_key": API_KEY,
            "startTime": start.strftime(fmt),
            "endTime": end.strftime(fmt),
        },
        timeout=30,
    )
    resp.raise_for_status()

    meters = resp.json()["powerDetails"]["meters"]
    production = next((m for m in meters if m["type"] == "Production"), None)
    if not production:
        return []

    results = []
    for entry in production["values"]:
        if entry.get("value") is None:
            continue
        local_dt = datetime.strptime(entry["date"], fmt).replace(tzinfo=AMSTERDAM)
        results.append((local_dt.astimezone(UTC), float(entry["value"])))
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


def months_to_fetch() -> list[tuple[int, int]]:
    now = datetime.now(AMSTERDAM)
    current = START_DATE
    months = []
    while (current.year, current.month) <= (now.year, now.month):
        months.append((current.year, current.month))
        # advance one month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def main():
    if not API_KEY:
        log.error("SOLAREDGE_API_KEY is not set")
        sys.exit(1)

    months = months_to_fetch()
    log.info(
        "Backfill range: %04d-%02d → %04d-%02d (%d months)",
        months[0][0],
        months[0][1],
        months[-1][0],
        months[-1][1],
        len(months),
    )

    with db_connect() as conn:
        skipped = 0
        fetched = 0
        for year, month in months:
            now = datetime.now(AMSTERDAM)
            is_current_month = year == now.year and month == now.month
            if not is_current_month and month_has_data(conn, year, month):
                skipped += 1
                continue

            log.info("Fetching %04d-%02d ...", year, month)
            try:
                rows = fetch_month(year, month)
            except httpx.HTTPStatusError as e:
                log.warning("  → HTTP %s, skipping (re-run to retry)", e.response.status_code)
                time.sleep(REQUEST_DELAY)
                continue

            if rows:
                upsert(conn, rows)
                log.info("  → %d rows inserted", len(rows))
                fetched += 1
            else:
                log.info("  → no data (panels not yet active?)")

            time.sleep(REQUEST_DELAY)

    log.info("Done. Fetched %d month(s), skipped %d already populated.", fetched, skipped)


if __name__ == "__main__":
    main()
