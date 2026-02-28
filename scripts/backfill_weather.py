#!/usr/bin/env python3
"""
Backfill historical hourly irradiance data from Open-Meteo ERA5 archive
into openmeteo.hourly.

Usage:
    .venv/bin/python backfill_weather.py

Fetches year by year from solar data start (2019-08-22) up to and including
yesterday. Run once; safe to re-run (upserts).
"""

import logging
import os
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import psycopg2
import psycopg2.extras

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

SITE_LAT = float(os.environ["SITE_LAT"])
SITE_LON = float(os.environ["SITE_LON"])
DATA_START = date.fromisoformat(os.environ["WEATHER_DATA_START"])

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HOURLY_VARS = "shortwave_radiation,direct_normal_irradiance,diffuse_radiation,temperature_2m"

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def fetch_year(start: date, end: date) -> list[tuple]:
    """Fetch hourly irradiance for [start, end] from Open-Meteo archive.
    Returns list of (measured_at_utc, shortwave, dni, diffuse).
    """
    params = {
        "latitude": SITE_LAT,
        "longitude": SITE_LON,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": HOURLY_VARS,
        "timezone": "Europe/Amsterdam",
    }
    resp = httpx.get(ARCHIVE_URL, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()["hourly"]

    # Use dict keyed on UTC to deduplicate DST fall-back hours (same local
    # time string appears twice; both parse to the same UTC with .replace())
    seen = {}
    for i, ts_str in enumerate(data["time"]):
        # Open-Meteo returns "YYYY-MM-DDTHH:MM" in requested timezone
        local_dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M").replace(tzinfo=AMSTERDAM)
        utc_dt = local_dt.astimezone(UTC)
        if utc_dt not in seen:
            seen[utc_dt] = (
                utc_dt,
                data["shortwave_radiation"][i],
                data["direct_normal_irradiance"][i],
                data["diffuse_radiation"][i],
                data["temperature_2m"][i],
            )
    return list(seen.values())


def upsert(conn, rows: list[tuple]):
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO openmeteo.hourly
                (measured_at, shortwave_radiation, direct_normal_irradiance, diffuse_radiation, temperature_2m)
            VALUES %s
            ON CONFLICT (measured_at) DO UPDATE SET
                shortwave_radiation      = EXCLUDED.shortwave_radiation,
                direct_normal_irradiance = EXCLUDED.direct_normal_irradiance,
                diffuse_radiation        = EXCLUDED.diffuse_radiation,
                temperature_2m           = EXCLUDED.temperature_2m
            """,
            rows,
        )
    conn.commit()


def main():
    yesterday = date.today() - timedelta(days=1)
    conn = psycopg2.connect(**DB)

    # Iterate year by year
    year_start = DATA_START
    while year_start <= yesterday:
        year_end = min(date(year_start.year, 12, 31), yesterday)

        log.info("Fetching %s → %s", year_start, year_end)
        rows = fetch_year(year_start, year_end)
        upsert(conn, rows)
        log.info("  Upserted %d rows", len(rows))

        year_start = date(year_start.year + 1, 1, 1)

    conn.close()
    log.info("Backfill complete.")


if __name__ == "__main__":
    main()
