#!/usr/bin/env python3
"""
Battery capacity sensitivity analysis for 2025.

Runs the home battery simulation across a range of capacities
to find the point of diminishing returns.

Assumes linear cost scaling with capacity, no charge/discharge losses.

Run on the Pi (or locally with DB accessible):
    .venv/bin/python simulate_battery_capacity.py
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

CAPACITIES = [0, 2, 4, 6, 8, 10, 12, 15, 20, 25, 30]


def fetch_hourly(conn):
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
                COALESCE(s.solar_kwh, 0),
                p.grid_import,
                p.grid_export
            FROM pureenergie.consumption p
            LEFT JOIN solar s
                ON s.hour = p.measured_at AT TIME ZONE 'Europe/Amsterdam'
            WHERE p.measured_at >= '2025-01-01 00:00:00 Europe/Amsterdam'
              AND p.measured_at <  '2026-01-01 00:00:00 Europe/Amsterdam'
            ORDER BY p.measured_at
        """)
        return [(float(r[0]), float(r[1]), float(r[2])) for r in cur.fetchall()]


def simulate(rows, capacity):
    soc = 0.0
    export_batt = import_batt = 0.0
    for solar, grid_import, grid_export in rows:
        ne, ni = grid_export, grid_import
        if grid_export > 0:
            charged = min(grid_export, capacity - soc)
            soc += charged
            ne = grid_export - charged
        if grid_import > 0:
            discharged = min(grid_import, soc)
            soc -= discharged
            ni = grid_import - discharged
        export_batt += ne
        import_batt += ni
    return export_batt, import_batt


def bar(value, scale=5, width=20):
    filled = min(int(value / scale), width)
    return "█" * filled + "░" * (width - filled)


def main():
    conn = psycopg2.connect(**DB)
    rows = fetch_hourly(conn)
    conn.close()
    print(f"Loaded {len(rows)} hourly intervals for 2025")

    total_solar = sum(r[0] for r in rows)

    results = []
    for cap in CAPACITIES:
        export, imp = simulate(rows, cap)
        sc = total_solar - export
        results.append((cap, sc, imp, export))

    print()
    print("=" * 72)
    print(f"  Capacity sensitivity — 2025  (solar: {total_solar:.0f} kWh)")
    print("=" * 72)
    print(
        f"  {'Cap':>5}  {'SC kWh':>7}  {'SC%':>5}  {'Import':>7}  "
        f"{'ΔSC/kWh':>8}  Marginal gain per kWh added"
    )
    print(f"  {'-' * 5}  {'-' * 7}  {'-' * 5}  {'-' * 7}  {'-' * 8}  {'-' * 30}")

    prev_cap, prev_sc = 0, results[0][1]
    for cap, sc, imp, export in results:
        delta_cap = cap - prev_cap
        delta_sc = sc - prev_sc
        if delta_cap > 0:
            marginal = delta_sc / delta_cap
            marginal_str = f"{marginal:>6.1f} kWh"
            b = bar(marginal, scale=5)
        else:
            marginal_str = "    —   "
            b = ""
        print(
            f"  {cap:>5}  {sc:>7.0f}  {sc / total_solar * 100:>4.0f}%  "
            f"{imp:>7.0f}  {marginal_str}  {b}"
        )
        prev_cap, prev_sc = cap, sc

    print()
    sc_0 = results[0][1]
    sc_10 = results[CAPACITIES.index(10)][1]
    sc_30 = results[CAPACITIES.index(30)][1]
    print(f"  No battery:   {sc_0:.0f} kWh self-consumed ({sc_0 / total_solar * 100:.0f}%)")
    print(f"  10 kWh:       {sc_10:.0f} kWh self-consumed ({sc_10 / total_solar * 100:.0f}%)")
    print(f"  30 kWh:       {sc_30:.0f} kWh self-consumed ({sc_30 / total_solar * 100:.0f}%)")
    print(f"  Theoretical max (infinite battery): {total_solar:.0f} kWh (100%)")
    print()


if __name__ == "__main__":
    main()
