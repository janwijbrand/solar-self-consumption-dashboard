import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg2
import psycopg2.extras
import pvlib
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

app = FastAPI()

TZ = ZoneInfo("Europe/Amsterdam")

BATTERY_KWH = float(os.environ.get("BATTERY_KWH", "8.0"))
BATTERY_START = datetime.fromisoformat(os.environ["BATTERY_START"]).replace(tzinfo=TZ)

DB = dict(
    host=os.environ.get("DB_HOST", "dsmrdb"),
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)


@contextmanager
def get_db():
    conn = psycopg2.connect(**DB)
    try:
        yield conn
    finally:
        conn.close()


def fetch_hourly_grid(conn, start: datetime, end: datetime):
    """Hourly (import_kwh, export_kwh) combined from pureenergie + DSMR.
    DSMR takes priority when both sources cover the same hour.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH pe AS (
                SELECT
                    measured_at AT TIME ZONE 'Europe/Amsterdam' AS hour,
                    grid_import  AS import_kwh,
                    grid_export  AS export_kwh
                FROM pureenergie.consumption
                WHERE measured_at >= %s AND measured_at < %s
            ),
            dsmr AS (
                SELECT
                    date_trunc('hour', read_at AT TIME ZONE 'Europe/Amsterdam') AS hour,
                    AVG(currently_delivered) AS import_kwh,
                    AVG(currently_returned)  AS export_kwh
                FROM dsmr_consumption_electricityconsumption
                WHERE read_at >= %s AND read_at < %s
                GROUP BY 1
            ),
            combined AS (
                SELECT hour, import_kwh, export_kwh, 1 AS src FROM dsmr
                UNION ALL
                SELECT hour, import_kwh, export_kwh, 2 AS src FROM pe
            )
            SELECT DISTINCT ON (hour) hour, import_kwh, export_kwh
            FROM combined
            ORDER BY hour, src
        """,
            (start, end, start, end),
        )
        return [(float(r[1] or 0), float(r[2] or 0)) for r in cur.fetchall()]


def _sim_step(soc: float, imp: float, exp: float, capacity: float):
    ni, ne = imp, exp
    if exp > 0:
        charged = min(exp, capacity - soc)
        soc += charged
        ne = exp - charged
    if imp > 0:
        discharged = min(imp, soc)
        soc -= discharged
        ni = imp - discharged
    return soc, ni, ne


def battery_soc_at(conn, end: datetime, battery_kwh: float) -> float:
    """SOC (kWh) of the battery at `end`, simulated from BATTERY_START."""
    rows = fetch_hourly_grid(conn, BATTERY_START, end)
    soc = 0.0
    for imp, exp in rows:
        soc, _, _ = _sim_step(soc, imp, exp, battery_kwh)
    return soc


def battery_stats_for_range(conn, start: datetime, end: datetime, battery_kwh: float) -> dict:
    """Simulate battery over a period; return simulated import/export totals."""
    start_soc = battery_soc_at(conn, start, battery_kwh)
    rows = fetch_hourly_grid(conn, start, end)
    soc = start_soc
    sim_imp_total = sim_exp_total = 0.0
    for imp, exp in rows:
        soc, ni, ne = _sim_step(soc, imp, exp, battery_kwh)
        sim_imp_total += ni
        sim_exp_total += ne
    return {
        "battery_kwh": battery_kwh,
        "grid_import_sim_kwh": round(sim_imp_total, 2),
        "grid_export_sim_kwh": round(sim_exp_total, 2),
    }


def enrich_with_battery(result: dict, start: datetime, end: datetime, battery_kwh: float) -> dict:
    with get_db() as conn:
        batt = battery_stats_for_range(conn, start, end, battery_kwh)
    solar = result.get("solar_kwh") or 0
    sim_imp = batt["grid_import_sim_kwh"]
    sim_exp = batt["grid_export_sim_kwh"]
    sc_sim = max(solar - sim_exp, 0)
    tc_sim = sc_sim + sim_imp
    result.update(
        {
            **batt,
            "self_consumed_sim_kwh": round(sc_sim, 2),
            "self_consumption_sim_pct": round(sc_sim / solar * 100) if solar else 0,
            "solar_fraction_sim_pct": round(sc_sim / tc_sim * 100) if tc_sim else 0,
            "grid_import_saved_kwh": round((result.get("grid_import_kwh") or 0) - sim_imp, 2),
        }
    )
    return result


GHI_MAX = float(os.environ.get("GHI_MAX", "900.0"))

SITE_LAT = float(os.environ.get("SITE_LAT", "52.0"))
SITE_LON = float(os.environ.get("SITE_LON", "4.5"))
PANEL_TILT = float(os.environ.get("PANEL_TILT", "35"))
PANEL_AZIMUTH = float(os.environ.get("PANEL_AZIMUTH", "180"))

# ── Forecast calibration (loaded lazily on first /api/forecast request) ────────

_forecast_calib: dict | None = None
_has_temp_col: bool | None = None


def _load_forecast_calibration() -> dict:
    """Compute per-month POA ceilings and hour×month GHI correction factors.

    Steps 1, 2, and 3 of the forecast accuracy plan are prepared here:
    - monthly_poa_max  — historical max POA per month (pvlib, step 1 for POA)
    - monthly_ghi_max  — historical max GHI per month (fallback, step 1)
    - calibration_ghi  — mean(production_w / ghi) per (month, hour) (step 2)

    All data comes from tables already in the DB; no new sources required.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            # Step 1: per-month GHI ceilings from historical observations
            cur.execute("""
                SELECT
                    EXTRACT(MONTH FROM measured_at AT TIME ZONE 'Europe/Amsterdam')::int,
                    MAX(shortwave_radiation)
                FROM openmeteo.hourly
                WHERE shortwave_radiation IS NOT NULL
                GROUP BY 1
            """)
            monthly_ghi_max = {int(r[0]): float(r[1]) for r in cur.fetchall() if r[1] is not None}

            # Historical clear-sky hours: averaged production + hourly irradiance
            cur.execute("""
                SELECT
                    date_trunc('hour', p.measured_at)   AS utc_hour,
                    AVG(p.power_w)                      AS avg_power_w,
                    MAX(w.shortwave_radiation)           AS ghi,
                    MAX(w.direct_normal_irradiance)      AS dni,
                    MAX(w.diffuse_radiation)             AS dhi
                FROM solaredge.production p
                JOIN openmeteo.hourly w
                  ON date_trunc('hour', p.measured_at) = w.measured_at
                WHERE w.shortwave_radiation > 50
                GROUP BY 1
                HAVING AVG(p.power_w) > 0
                ORDER BY 1
            """)
            hist_rows = cur.fetchall()

    monthly_poa_max: dict[int, float] = {}
    calibration_ghi: dict[tuple[int, int], float] = {}

    if hist_rows:
        utc_index = pd.to_datetime([r[0] for r in hist_rows], utc=True)
        ams_index = utc_index.tz_convert("Europe/Amsterdam")

        ghi_s = pd.Series([float(r[2] or 0) for r in hist_rows], index=ams_index)
        dni_s = pd.Series([float(r[3] or 0) for r in hist_rows], index=ams_index)
        dhi_s = pd.Series([float(r[4] or 0) for r in hist_rows], index=ams_index)

        # Step 3: compute POA irradiance for all historical rows (pvlib)
        location = pvlib.location.Location(SITE_LAT, SITE_LON, tz="Europe/Amsterdam")
        solpos = location.get_solarposition(ams_index)
        dni_extra = pvlib.irradiance.get_extra_radiation(ams_index)
        poa_s = (
            pvlib.irradiance.get_total_irradiance(
                surface_tilt=PANEL_TILT,
                surface_azimuth=PANEL_AZIMUTH,
                solar_zenith=solpos["apparent_zenith"],
                solar_azimuth=solpos["azimuth"],
                dni=dni_s,
                ghi=ghi_s,
                dhi=dhi_s,
                dni_extra=dni_extra,
                model="haydavies",
            )["poa_global"]
            .clip(lower=0)
            .fillna(0)
        )

        # Monthly POA ceiling (step 1 applied to POA)
        for m in range(1, 13):
            mask = ams_index.month == m
            if mask.any():
                monthly_poa_max[m] = float(poa_s[mask].max())

        # Step 2: GHI correction factor per (month, hour)
        prod_s = pd.Series([float(r[1]) for r in hist_rows], index=ams_index)
        df = pd.DataFrame(
            {
                "month": ams_index.month,
                "hour": ams_index.hour,
                "ghi": ghi_s.values,
                "production": prod_s.values,
            }
        )
        df = df[df["ghi"] > 50].copy()
        df["ratio"] = df["production"] / df["ghi"]
        calibration_ghi = df.groupby(["month", "hour"])["ratio"].mean().to_dict()

    return {
        "monthly_ghi_max": monthly_ghi_max,
        "monthly_poa_max": monthly_poa_max,
        "calibration_ghi": calibration_ghi,
    }


def _get_calibration() -> dict:
    global _forecast_calib
    if _forecast_calib is None:
        try:
            _forecast_calib = _load_forecast_calibration()
        except Exception as e:
            log.warning("Could not load forecast calibration: %s", e)
            _forecast_calib = {
                "monthly_ghi_max": {},
                "monthly_poa_max": {},
                "calibration_ghi": {},
            }
    return _forecast_calib


def _check_temp_col() -> bool:
    """Return True if temperature_2m column exists in openmeteo.hourly."""
    global _has_temp_col
    if _has_temp_col is None:
        try:
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'openmeteo' AND table_name = 'hourly'
                          AND column_name = 'temperature_2m'
                    """)
                    _has_temp_col = cur.fetchone() is not None
        except Exception:
            _has_temp_col = False
    return _has_temp_col


@app.get("/api/forecast")
def solar_forecast():
    """Next 24 hours of solar potential as % of calibrated clear-sky maximum.

    Uses pvlib to compute plane-of-array (POA) irradiance for the tilted panel
    surface (step 3), normalised by the per-month historical POA ceiling (step 1).
    Temperature derating is applied when temperature_2m data is available (step 4).
    The hour×month GHI calibration table (step 2) is also computed at startup
    but POA already accounts for panel geometry so it is not applied on top.
    """
    calib = _get_calibration()
    has_temp = _check_temp_col()

    now = datetime.now(TZ)
    start = datetime(now.year, now.month, now.day, now.hour, tzinfo=TZ)
    end = start + timedelta(hours=24)

    temp_col = "temperature_2m" if has_temp else "NULL"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    measured_at,
                    shortwave_radiation,
                    direct_normal_irradiance,
                    diffuse_radiation,
                    {temp_col}
                FROM openmeteo.hourly
                WHERE measured_at >= %s AND measured_at < %s
                ORDER BY measured_at
                """,
                (start, end),
            )
            rows = cur.fetchall()

    if not rows:
        return {"hours": []}

    utc_index = pd.to_datetime([r[0] for r in rows], utc=True)
    ams_index = utc_index.tz_convert("Europe/Amsterdam")

    ghi_s = pd.Series([float(r[1] or 0) for r in rows], index=ams_index)
    dni_s = pd.Series([float(r[2] or 0) for r in rows], index=ams_index)
    dhi_s = pd.Series([float(r[3] or 0) for r in rows], index=ams_index)
    temp_s = pd.Series([float(r[4]) if r[4] is not None else 25.0 for r in rows], index=ams_index)

    # Step 3: POA irradiance on the tilted panel surface (pvlib Hay-Davies)
    location = pvlib.location.Location(SITE_LAT, SITE_LON, tz="Europe/Amsterdam")
    solpos = location.get_solarposition(ams_index)
    dni_extra = pvlib.irradiance.get_extra_radiation(ams_index)
    poa_s = (
        pvlib.irradiance.get_total_irradiance(
            surface_tilt=PANEL_TILT,
            surface_azimuth=PANEL_AZIMUTH,
            solar_zenith=solpos["apparent_zenith"],
            solar_azimuth=solpos["azimuth"],
            dni=dni_s,
            ghi=ghi_s,
            dhi=dhi_s,
            dni_extra=dni_extra,
            model="haydavies",
        )["poa_global"]
        .clip(lower=0)
        .fillna(0)
    )

    monthly_poa_max = calib["monthly_poa_max"]
    monthly_ghi_max = calib["monthly_ghi_max"]

    hours = []
    for local_ts, poa_val, ghi_val, temp_val in zip(ams_index, poa_s, ghi_s, temp_s):
        month = local_ts.month

        # Step 1: per-month normalisation; prefer POA ceiling, fall back to GHI / global
        ceiling = monthly_poa_max.get(month) or monthly_ghi_max.get(month) or GHI_MAX
        raw_ratio = float(poa_val) / ceiling if ceiling > 0 else 0.0

        # Step 4: temperature derating — standard PV coefficient −0.4 %/°C above 25 °C
        temp_factor = 1.0 - max(0.0, float(temp_val) - 25.0) * 0.004

        hours.append(
            {
                "hour": local_ts.strftime("%H"),
                "potential_pct": min(100, round(raw_ratio * temp_factor * 100)),
                "is_daytime": float(poa_val) > 0,
            }
        )

    return {"hours": hours}


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the energy dashboard API"}


@app.get("/api/current")
def current_power(battery_kwh: float = BATTERY_KWH):
    """Latest solar production, live grid power, and simulated battery SOC."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Latest solar reading (within last 30 min, else 0)
            cur.execute("""
                SELECT power_w, measured_at
                FROM solaredge.production
                WHERE measured_at >= NOW() - INTERVAL '30 minutes'
                ORDER BY measured_at DESC
                LIMIT 1
            """)
            solar_row = cur.fetchone()

            # Latest DSMR reading (within last 5 min)
            cur.execute("""
                SELECT
                    currently_delivered * 1000 AS grid_import_w,
                    currently_returned  * 1000 AS grid_export_w,
                    read_at
                FROM dsmr_consumption_electricityconsumption
                WHERE read_at >= NOW() - INTERVAL '5 minutes'
                ORDER BY read_at DESC
                LIMIT 1
            """)
            dsmr_row = cur.fetchone()

        soc_kwh = battery_soc_at(conn, datetime.now(TZ), battery_kwh) if battery_kwh > 0 else None

    solar_w = float(solar_row["power_w"]) if solar_row else 0.0
    grid_import_w = float(dsmr_row["grid_import_w"]) if dsmr_row else None
    grid_export_w = float(dsmr_row["grid_export_w"]) if dsmr_row else None

    # Negative consumption is physically impossible and always indicates stale
    # solar data (e.g. sudden ramp-up seen by the meter before SolarEdge reports it).
    consumption_w = None
    consumption_stale = False
    if grid_import_w is not None:
        value = solar_w + grid_import_w - grid_export_w
        if value < 0:
            consumption_stale = True
        else:
            consumption_w = value

    return {
        "solar_w": solar_w,
        "grid_import_w": grid_import_w,
        "grid_export_w": grid_export_w,
        "consumption_w": consumption_w,
        "consumption_stale": consumption_stale,
        "solar_at": solar_row["measured_at"].isoformat() if solar_row else None,
        "grid_at": dsmr_row["read_at"].isoformat() if dsmr_row else None,
        "battery_kwh": battery_kwh if battery_kwh > 0 else None,
        "battery_soc_kwh": round(soc_kwh, 2) if soc_kwh is not None else None,
        "battery_soc_pct": round(soc_kwh / battery_kwh * 100) if soc_kwh is not None else None,
    }


def summary_for_range(start: datetime, end: datetime) -> dict:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(power_w) * 0.25 / 1000, 0)
                FROM solaredge.production
                WHERE measured_at >= %s AND measured_at < %s
            """,
                (start, end),
            )
            solar_kwh = float(cur.fetchone()[0])

            cur.execute(
                """
                SELECT MIN(read_at), COUNT(*)
                FROM dsmr_consumption_electricityconsumption
                WHERE read_at >= %s AND read_at < %s
            """,
                (start, end),
            )
            dsmr_first, dsmr_count = cur.fetchone()

            grid_import = grid_export = None
            sources = []

            if dsmr_count:
                cur.execute(
                    """
                    SELECT
                        MAX(delivered_1 + delivered_2) - MIN(delivered_1 + delivered_2),
                        MAX(returned_1  + returned_2)  - MIN(returned_1  + returned_2)
                    FROM dsmr_consumption_electricityconsumption
                    WHERE read_at >= %s AND read_at < %s
                """,
                    (start, end),
                )
                row = cur.fetchone()
                grid_import = float(row[0])
                grid_export = float(row[1])
                sources.append("DSMR")

            pe_end = dsmr_first if dsmr_count else end
            cur.execute(
                """
                SELECT COALESCE(SUM(grid_import), 0), COALESCE(SUM(grid_export), 0), COUNT(*)
                FROM pureenergie.consumption
                WHERE measured_at >= %s AND measured_at < %s
            """,
                (start, pe_end),
            )
            pe_import, pe_export, pe_count = cur.fetchone()
            if pe_count:
                grid_import = (grid_import or 0) + float(pe_import)
                grid_export = (grid_export or 0) + float(pe_export)
                sources.append("Pure Energie")

    self_consumed = max(solar_kwh - (grid_export or 0), 0) if grid_export is not None else None
    total_consumed = (self_consumed + grid_import) if self_consumed is not None else None

    return {
        "solar_kwh": round(solar_kwh, 2),
        "grid_import_kwh": round(grid_import, 2) if grid_import is not None else None,
        "grid_export_kwh": round(grid_export, 2) if grid_export is not None else None,
        "self_consumed_kwh": round(self_consumed, 2) if self_consumed is not None else None,
        "total_consumed_kwh": round(total_consumed, 2) if total_consumed is not None else None,
        "self_consumption_pct": round(self_consumed / solar_kwh * 100)
        if self_consumed and solar_kwh
        else 0,
        "solar_fraction_pct": round(self_consumed / total_consumed * 100)
        if self_consumed and total_consumed
        else 0,
        "sources": sources,
    }


@app.get("/api/today")
def today_summary(battery_kwh: float = BATTERY_KWH):
    """Today's energy breakdown in kWh."""
    now = datetime.now(TZ)
    start = datetime(now.year, now.month, now.day, tzinfo=TZ)
    end = start + timedelta(days=1)
    return enrich_with_battery(summary_for_range(start, end), start, end, battery_kwh)


@app.get("/api/week")
def week_summary(battery_kwh: float = BATTERY_KWH):
    """This week's energy breakdown in kWh (Mon–today)."""
    now = datetime.now(TZ)
    today = datetime(now.year, now.month, now.day, tzinfo=TZ)
    start = today - timedelta(days=today.weekday())
    end = today + timedelta(days=1)
    return enrich_with_battery(summary_for_range(start, end), start, end, battery_kwh)


@app.get("/api/today/chart")
def today_chart(battery_kwh: float = BATTERY_KWH):
    """15-minute solar and grid data for today's chart."""
    now = datetime.now(TZ)
    start = datetime(now.year, now.month, now.day, tzinfo=TZ)
    end = start + timedelta(days=1)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    measured_at AT TIME ZONE 'Europe/Amsterdam' AS ts,
                    power_w
                FROM solaredge.production
                WHERE measured_at >= %s AND measured_at < %s
                ORDER BY measured_at
            """,
                (start, end),
            )
            solar_rows = cur.fetchall()

            # DSMR aggregated to 15-min buckets
            cur.execute(
                """
                SELECT
                    date_trunc('hour', read_at AT TIME ZONE 'Europe/Amsterdam')
                    + INTERVAL '15 min' * FLOOR(
                        EXTRACT(minute FROM read_at AT TIME ZONE 'Europe/Amsterdam') / 15
                    ) AS bucket,
                    AVG(currently_delivered) * 1000 AS import_w,
                    AVG(currently_returned)  * 1000 AS export_w
                FROM dsmr_consumption_electricityconsumption
                WHERE read_at >= %s AND read_at < %s
                GROUP BY bucket
                ORDER BY bucket
            """,
                (start, end),
            )
            dsmr_rows = cur.fetchall()

        midnight_soc = battery_soc_at(conn, start, battery_kwh)

    solar_map = {ts.replace(second=0, microsecond=0): float(power_w) for ts, power_w in solar_rows}
    dsmr_map = {row[0]: (float(row[1]), float(row[2])) for row in dsmr_rows}

    points = []
    for bucket in sorted(set(solar_map) | set(dsmr_map)):
        dsmr = dsmr_map.get(bucket)
        points.append(
            {
                "time": bucket.strftime("%H:%M"),
                "solar_w": solar_map.get(bucket, 0.0),
                "grid_import_w": round(dsmr[0]) if dsmr else None,
                "grid_export_w": round(dsmr[1]) if dsmr else None,
            }
        )

    # Battery simulation overlay on 15-min points
    soc = midnight_soc
    for p in points:
        if p["grid_import_w"] is not None:
            imp_kwh = p["grid_import_w"] / 1000 * 0.25
            exp_kwh = p["grid_export_w"] / 1000 * 0.25
            soc, ni, ne = _sim_step(soc, imp_kwh, exp_kwh, battery_kwh)
            p["grid_import_sim_w"] = round(ni / 0.25 * 1000)
            p["grid_export_sim_w"] = round(ne / 0.25 * 1000)
        else:
            p["grid_import_sim_w"] = None
            p["grid_export_sim_w"] = None

    return {"points": points}


# Serve Vue build — must come after API routes
STATIC_DIR = Path(__file__).parent / "dist"

if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str):
        return FileResponse(STATIC_DIR / "index.html")
