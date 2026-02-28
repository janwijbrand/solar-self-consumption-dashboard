#!/usr/bin/env python3
"""
Collect hourly irradiance data from Open-Meteo (recent past + short forecast)
into openmeteo.hourly. Run every 15 minutes via cron alongside collect.py.

Fetches a 4-day window (2 days back, today, 1 day ahead) on every run.
The 2-day overlap ensures the archive catches any delayed ERA5 updates;
the 1-day lookahead provides forecast data for the dashboard.
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

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARS = "shortwave_radiation,direct_normal_irradiance,diffuse_radiation"

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def fetch(start: date, end: date) -> list[tuple]:
    params = {
        "latitude": SITE_LAT,
        "longitude": SITE_LON,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": HOURLY_VARS,
        "timezone": "Europe/Amsterdam",
    }
    resp = httpx.get(FORECAST_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()["hourly"]

    seen = {}
    for i, ts_str in enumerate(data["time"]):
        local_dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M").replace(tzinfo=AMSTERDAM)
        utc_dt = local_dt.astimezone(UTC)
        if utc_dt not in seen:
            seen[utc_dt] = (
                utc_dt,
                data["shortwave_radiation"][i],
                data["direct_normal_irradiance"][i],
                data["diffuse_radiation"][i],
            )
    return list(seen.values())


def upsert(conn, rows: list[tuple]):
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO openmeteo.hourly
                (measured_at, shortwave_radiation, direct_normal_irradiance, diffuse_radiation)
            VALUES %s
            ON CONFLICT (measured_at) DO UPDATE SET
                shortwave_radiation      = EXCLUDED.shortwave_radiation,
                direct_normal_irradiance = EXCLUDED.direct_normal_irradiance,
                diffuse_radiation        = EXCLUDED.diffuse_radiation
            """,
            rows,
        )
    conn.commit()


def main():
    today = date.today()
    start = today - timedelta(days=2)
    end = today + timedelta(days=1)

    log.info("Fetching %s → %s", start, end)
    rows = fetch(start, end)
    upsert(psycopg2.connect(**DB), rows)
    log.info("Upserted %d rows (forecast through %s)", len(rows), end)


if __name__ == "__main__":
    main()
