#!/usr/bin/env python3
"""
Collect Dutch grid carbon intensity from NED (Nationaal Energie Dashboard)
into ned.utilizations. Run every 15 minutes via cron alongside collect.py.

Uses type=27 (ElectricityMix) at 15-minute granularity — a single API call
that returns the complete Dutch grid mix (fossil + renewables + nuclear + CHP)
with a pre-computed emissionfactor in kg CO₂/kWh.

Fetches a 2-day rolling window (yesterday + today) on every run to catch
any retroactive updates. NED publishes with ~30 minute lag.
Safe to re-run — upserts on (measured_at, type_id).

API parameters confirmed via probe on 2026-03-03:
  type=27        ElectricityMix (complete grid mix)
  granularity=4  15-minute intervals
  granularitytimezone=1   CET/CEST (all lowercase — critical)
  classification=2  Current (near-realtime)
  activity=1     Providing (generation)
  validfrom filters accept YYYY-MM-DD format only (not datetime)

Emission unit: kg CO₂ per interval
Volume unit:   kWh
EmissionFactor: kg CO₂/kWh  (× 1000 = g/kWh, stored in ned.carbon_intensity view)
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

NED_API_KEY = os.environ["NED_API_KEY"]
NED_BASE = "https://api.ned.nl/v1"

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
HEADERS = {"X-AUTH-TOKEN": NED_API_KEY, "Accept": "application/ld+json"}

# The only type queried — ElectricityMix includes the full Dutch grid.
NED_TYPE = 27


def fetch_utilizations(start: date, end: date) -> list[dict]:
    """Fetch ElectricityMix utilizations for [start, end) from the NED API.

    Paginates automatically. The API requires YYYY-MM-DD format for dates.
    """
    all_items: list[dict] = []
    page = 1
    while True:
        params = {
            "point": 0,  # Netherlands total
            "type": NED_TYPE,  # ElectricityMix
            "granularity": 4,  # 15-minute
            "granularitytimezone": 1,  # CET/CEST (must be lowercase key)
            "classification": 2,  # Current (near-realtime)
            "activity": 1,  # Providing (generation)
            "validfrom[after]": start.isoformat(),
            "validfrom[strictly_before]": end.isoformat(),
            "itemsPerPage": 200,
            "page": page,
        }
        resp = httpx.get(f"{NED_BASE}/utilizations", headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        members = data.get("hydra:member", [])
        if not members:
            break
        all_items.extend(members)

        if len(members) < 200:
            break
        page += 1

    return all_items


def parse_rows(items: list[dict]) -> list[tuple]:
    """Convert raw API items to DB row tuples.

    Returns list of (measured_at, type_id, type_name, volume_kwh,
                     emission_kg, emission_factor, percentage).
    """
    rows = []
    for item in items:
        valid_from_str = item.get("validfrom")
        if not valid_from_str:
            continue

        # API returns ISO 8601 with UTC offset, e.g. "2026-03-03T12:00:00+00:00"
        local_dt = datetime.fromisoformat(valid_from_str)
        utc_dt = local_dt.astimezone(UTC)

        # type is an IRI string like "/v1/types/27"
        type_iri = item.get("type", "")
        try:
            type_id = int(str(type_iri).rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            continue
        type_name = "ElectricityMix"

        volume_kwh = item.get("volume")
        volume_kwh = float(volume_kwh) if volume_kwh is not None else None

        emission_raw = item.get("emission")
        emission_kg = float(emission_raw) if emission_raw is not None else None

        ef_raw = item.get("emissionfactor")
        emission_factor = float(ef_raw) if ef_raw is not None else None

        pct_raw = item.get("percentage")
        percentage = float(pct_raw) if pct_raw is not None else None

        rows.append(
            (utc_dt, type_id, type_name, volume_kwh, emission_kg, emission_factor, percentage)
        )
    return rows


def upsert(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO ned.utilizations
                (measured_at, type_id, type_name, volume_kwh, emission_kg, emission_factor, percentage)
            VALUES %s
            ON CONFLICT (measured_at, type_id) DO UPDATE SET
                type_name       = EXCLUDED.type_name,
                volume_kwh      = EXCLUDED.volume_kwh,
                emission_kg     = EXCLUDED.emission_kg,
                emission_factor = EXCLUDED.emission_factor,
                percentage      = EXCLUDED.percentage
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def main():
    today = date.today()
    yesterday = today - timedelta(days=1)

    log.info("Fetching NED ElectricityMix %s → %s", yesterday, today + timedelta(days=1))
    items = fetch_utilizations(yesterday, today + timedelta(days=1))
    rows = parse_rows(items)

    conn = psycopg2.connect(**DB)
    n = upsert(conn, rows)
    conn.close()
    log.info("ned: upserted %d rows from %d API items", n, len(items))


if __name__ == "__main__":
    main()
