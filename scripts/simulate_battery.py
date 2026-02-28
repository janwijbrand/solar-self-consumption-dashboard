#!/usr/bin/env python3
"""
Home battery simulation for 2025.

Simulates a 10 kWh battery (no losses) installed Jan 1 2025,
and calculates how it would have affected self-consumption.

Uses:
  - pureenergie.consumption: hourly grid import/export
  - solaredge.production:    15-min solar production (aggregated to hourly)

Run on the Pi (or locally with DB accessible):
    .venv/bin/python simulate_battery.py
"""

import os

import psycopg2

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)

BATTERY_CAPACITY = 8.0  # kWh
START_SOC = 0.0  # start empty


def fetch_hourly(conn):
    """Hourly solar production + grid import/export for 2025."""
    with conn.cursor() as cur:
        cur.execute("""
            WITH solar AS (
                SELECT
                    date_trunc('hour', measured_at AT TIME ZONE 'Europe/Amsterdam') AS hour,
                    SUM(power_w) * 0.25 / 1000 AS solar_kwh
                FROM solaredge.production
                WHERE measured_at >= '2025-01-01 00:00:00 Europe/Amsterdam'
                  AND measured_at <  '2026-01-01 00:00:00 Europe/Amsterdam'
                GROUP BY 1
            )
            SELECT
                p.measured_at AT TIME ZONE 'Europe/Amsterdam' AS hour,
                COALESCE(s.solar_kwh, 0)  AS solar_kwh,
                p.grid_import             AS grid_import_kwh,
                p.grid_export             AS grid_export_kwh
            FROM pureenergie.consumption p
            LEFT JOIN solar s
                ON s.hour = p.measured_at AT TIME ZONE 'Europe/Amsterdam'
            WHERE p.measured_at >= '2025-01-01 00:00:00 Europe/Amsterdam'
              AND p.measured_at <  '2026-01-01 00:00:00 Europe/Amsterdam'
            ORDER BY p.measured_at
        """)
        return cur.fetchall()


def simulate(rows):
    soc = START_SOC

    total_solar = 0.0
    total_export_orig = 0.0
    total_import_orig = 0.0
    total_export_batt = 0.0
    total_import_batt = 0.0
    total_charged = 0.0
    total_discharged = 0.0

    monthly = {}

    for hour, solar, grid_import, grid_export in rows:
        solar = float(solar)
        grid_import = float(grid_import)
        grid_export = float(grid_export)

        total_solar += solar
        total_export_orig += grid_export
        total_import_orig += grid_import

        # Simulate battery
        new_export = grid_export
        new_import = grid_import

        if grid_export > 0:
            # Surplus: charge battery
            charged = min(grid_export, BATTERY_CAPACITY - soc)
            soc += charged
            new_export = grid_export - charged
            total_charged += charged

        if grid_import > 0:
            # Deficit: discharge battery
            discharged = min(grid_import, soc)
            soc -= discharged
            new_import = grid_import - discharged
            total_discharged += discharged

        total_export_batt += new_export
        total_import_batt += new_import

        # Monthly totals
        month = hour.strftime("%Y-%m")
        if month not in monthly:
            monthly[month] = dict(
                solar=0, export_orig=0, import_orig=0, export_batt=0, import_batt=0
            )
        monthly[month]["solar"] += solar
        monthly[month]["export_orig"] += grid_export
        monthly[month]["import_orig"] += grid_import
        monthly[month]["export_batt"] += new_export
        monthly[month]["import_batt"] += new_import

    return dict(
        total_solar=total_solar,
        export_orig=total_export_orig,
        import_orig=total_import_orig,
        export_batt=total_export_batt,
        import_batt=total_import_batt,
        total_charged=total_charged,
        total_discharged=total_discharged,
        monthly=monthly,
    )


def pct(part, total):
    return f"{part / total * 100:.0f}%" if total else "n/a"


def main():
    conn = psycopg2.connect(**DB)
    rows = fetch_hourly(conn)
    conn.close()
    print(f"Loaded {len(rows)} hourly intervals")

    r = simulate(rows)

    sc_orig = r["total_solar"] - r["export_orig"]
    sc_batt = r["total_solar"] - r["export_batt"]

    print()
    print("=" * 56)
    print(f"  Battery simulation: 2025, {BATTERY_CAPACITY:.0f} kWh, no losses")
    print("=" * 56)
    print(f"  Solar produced:        {r['total_solar']:8.0f} kWh")
    print()
    print("  WITHOUT battery:")
    print(
        f"    Self-consumed:       {sc_orig:8.0f} kWh  ({pct(sc_orig, r['total_solar'])} of solar)"
    )
    print(f"    Exported to grid:    {r['export_orig']:8.0f} kWh")
    print(f"    Imported from grid:  {r['import_orig']:8.0f} kWh")
    print()
    print(f"  WITH {BATTERY_CAPACITY:.0f} kWh battery:")
    print(
        f"    Self-consumed:       {sc_batt:8.0f} kWh  ({pct(sc_batt, r['total_solar'])} of solar)"
    )
    print(f"    Exported to grid:    {r['export_batt']:8.0f} kWh")
    print(f"    Imported from grid:  {r['import_batt']:8.0f} kWh")
    print(f"    Battery charged:     {r['total_charged']:8.0f} kWh")
    print(f"    Battery discharged:  {r['total_discharged']:8.0f} kWh")
    print()
    print("  Improvement:")
    print(
        f"    Extra self-consumed: {sc_batt - sc_orig:8.0f} kWh  (+{pct(sc_batt - sc_orig, sc_orig)})"
    )
    print(
        f"    Grid import reduced: {r['import_orig'] - r['import_batt']:8.0f} kWh  (-{pct(r['import_orig'] - r['import_batt'], r['import_orig'])})"
    )
    print()

    print(f"  {'Month':<8}  {'Solar':>6}  {'SC%':>5}  {'SC% +batt':>9}  {'Δ':>5}")
    print(f"  {'-' * 8}  {'-' * 6}  {'-' * 5}  {'-' * 9}  {'-' * 5}")
    for month, m in sorted(r["monthly"].items()):
        sc0 = m["solar"] - m["export_orig"]
        sc1 = m["solar"] - m["export_batt"]
        p0 = f"{sc0 / m['solar'] * 100:.0f}%" if m["solar"] else "—"
        p1 = f"{sc1 / m['solar'] * 100:.0f}%" if m["solar"] else "—"
        delta = f"+{(sc1 - sc0) / m['solar'] * 100:.0f}pp" if m["solar"] else "—"
        print(f"  {month:<8}  {m['solar']:>6.0f}  {p0:>5}  {p1:>9}  {delta:>5}")


if __name__ == "__main__":
    main()
