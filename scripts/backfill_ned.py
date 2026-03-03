#!/usr/bin/env python3
"""
Backfill historical Dutch grid carbon intensity from NED (Nationaal Energie Dashboard)
into ned.utilizations.

Usage:
    NED_BACKFILL_START=2024-01-01 .venv/bin/python scripts/backfill_ned.py

Iterates week by week from NED_BACKFILL_START up to yesterday.
Safe to re-run — upserts on (measured_at, type_id).

Uses type=27 (ElectricityMix) at 15-minute granularity (same as collect_ned.py).
Historical availability of Current (classification=2) data is unknown —
if a week returns no data, try classification=3 (Backcast) instead by setting
NED_CLASSIFICATION=3.
"""

import logging
import os
from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg2
import psycopg2.extras

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

NED_API_KEY = os.environ["NED_API_KEY"]
NED_BASE = "https://api.ned.nl/v1"
NED_BACKFILL_START = date.fromisoformat(os.environ.get("NED_BACKFILL_START", "2024-01-01"))
NED_CLASSIFICATION = int(os.environ.get("NED_CLASSIFICATION", "2"))

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)

HEADERS = {"X-AUTH-TOKEN": NED_API_KEY, "Accept": "application/ld+json"}


def fetch_utilizations(start: date, end: date) -> list[dict]:
    all_items: list[dict] = []
    page = 1
    while True:
        params = {
            "point": 0,
            "type": 27,  # ElectricityMix
            "granularity": 4,  # 15-minute
            "granularitytimezone": 1,  # CET/CEST
            "classification": NED_CLASSIFICATION,
            "activity": 1,  # Providing
            "validfrom[after]": start.isoformat(),
            "validfrom[strictly_before]": end.isoformat(),
            "itemsPerPage": 200,
            "page": page,
        }
        resp = httpx.get(f"{NED_BASE}/utilizations", headers=HEADERS, params=params, timeout=60)
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
    rows = []
    for item in items:
        valid_from_str = item.get("validfrom")
        if not valid_from_str:
            continue

        local_dt = datetime.fromisoformat(valid_from_str)
        utc_dt = local_dt.astimezone(UTC)

        type_iri = item.get("type", "")
        try:
            type_id = int(str(type_iri).rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            continue

        volume_kwh = item.get("volume")
        volume_kwh = float(volume_kwh) if volume_kwh is not None else None

        emission_raw = item.get("emission")
        emission_kg = float(emission_raw) if emission_raw is not None else None

        ef_raw = item.get("emissionfactor")
        emission_factor = float(ef_raw) if ef_raw is not None else None

        pct_raw = item.get("percentage")
        percentage = float(pct_raw) if pct_raw is not None else None

        rows.append(
            (
                utc_dt,
                type_id,
                "ElectricityMix",
                volume_kwh,
                emission_kg,
                emission_factor,
                percentage,
            )
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
    yesterday = date.today() - timedelta(days=1)
    conn = psycopg2.connect(**DB)

    week_start = NED_BACKFILL_START
    while week_start <= yesterday:
        week_end = min(week_start + timedelta(days=7), yesterday + timedelta(days=1))

        log.info("Fetching %s → %s", week_start, week_end)
        try:
            items = fetch_utilizations(week_start, week_end)
            rows = parse_rows(items)
            n = upsert(conn, rows)
            log.info("  Upserted %d rows from %d items", n, len(items))
        except httpx.HTTPStatusError as e:
            log.warning(
                "  HTTP %s for %s → %s — skipping", e.response.status_code, week_start, week_end
            )

        week_start = week_end

    conn.close()
    log.info("Backfill complete.")


if __name__ == "__main__":
    main()
