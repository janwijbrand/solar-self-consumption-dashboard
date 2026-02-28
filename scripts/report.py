#!/usr/bin/env python3
"""
Energy breakdown report: solar production, self-consumption, and grid usage.

Usage:
    python report.py                            # today
    python report.py today
    python report.py yesterday
    python report.py last-hour
    python report.py this-week
    python report.py last-week
    python report.py this-month
    python report.py last-month
    python report.py this-year
    python report.py last-year
    python report.py 2025-06-01 2025-06-30     # date range
    python report.py 2025-06-01                # single date
    python report.py 09:30 - 12:30             # time window today
    python report.py 2026-02-24 09:30 - 12:30  # time window on a date
"""

import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2

TZ = ZoneInfo("Europe/Amsterdam")

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)

TIME_RE = r"\d{1,2}:\d{2}"
DATE_RE = r"\d{4}-\d{2}-\d{2}"


def day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=TZ)


def parse_period(args: list[str]) -> tuple[datetime, datetime]:
    now = datetime.now(TZ)
    today = now.date()
    text = " ".join(args).strip() if args else "today"

    # last-hour
    if text == "last-hour":
        return now - timedelta(hours=1), now

    # Keywords
    keyword = text.lower()
    if keyword == "today":
        return day_start(today), day_start(today + timedelta(days=1))
    if keyword == "yesterday":
        d = today - timedelta(days=1)
        return day_start(d), day_start(today)
    if keyword == "this-week":
        start = today - timedelta(days=today.weekday())
        return day_start(start), day_start(today + timedelta(days=1))
    if keyword == "last-week":
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=7)
        return day_start(start), day_start(end)
    if keyword == "this-month":
        return day_start(today.replace(day=1)), day_start(today + timedelta(days=1))
    if keyword == "last-month":
        first = today.replace(day=1) - timedelta(days=1)
        return day_start(first.replace(day=1)), day_start(today.replace(day=1))
    if keyword == "this-year":
        return day_start(today.replace(month=1, day=1)), day_start(today + timedelta(days=1))
    if keyword == "last-year":
        y = today.year - 1
        return day_start(date(y, 1, 1)), day_start(date(y + 1, 1, 1))

    def parse_time(t: str) -> datetime:
        return datetime.strptime(t.strip().zfill(5), "%H:%M").replace(
            year=today.year, month=today.month, day=today.day, tzinfo=TZ
        )

    # HH:MM - HH:MM  (today)
    m = re.fullmatch(rf"({TIME_RE})\s*-\s*({TIME_RE})", text)
    if m:
        return parse_time(m.group(1)), parse_time(m.group(2))

    # YYYY-MM-DD HH:MM - HH:MM
    m = re.fullmatch(rf"({DATE_RE})\s+({TIME_RE})\s*-\s*({TIME_RE})", text)
    if m:
        d = date.fromisoformat(m.group(1))
        t1 = datetime.strptime(m.group(2).zfill(5), "%H:%M").replace(
            year=d.year, month=d.month, day=d.day, tzinfo=TZ
        )
        t2 = datetime.strptime(m.group(3).zfill(5), "%H:%M").replace(
            year=d.year, month=d.month, day=d.day, tzinfo=TZ
        )
        return t1, t2

    # YYYY-MM-DD YYYY-MM-DD
    m = re.fullmatch(rf"({DATE_RE})\s+({DATE_RE})", text)
    if m:
        start = date.fromisoformat(m.group(1))
        end = date.fromisoformat(m.group(2))
        return day_start(start), day_start(end + timedelta(days=1))

    # YYYY-MM-DD
    m = re.fullmatch(DATE_RE, text)
    if m:
        d = date.fromisoformat(text)
        return day_start(d), day_start(d + timedelta(days=1))

    raise ValueError(f"Unknown period: {text!r}")


def query(conn, start: datetime, end: datetime) -> dict:
    with conn.cursor() as cur:
        # Solar: sum of 15-min power readings → kWh
        cur.execute(
            """
            SELECT COALESCE(SUM(power_w) * 0.25 / 1000, 0)
            FROM solaredge.production
            WHERE measured_at >= %s AND measured_at < %s
        """,
            (start, end),
        )
        solar_kwh = float(cur.fetchone()[0])

        # DSMR: find actual coverage within the requested range
        cur.execute(
            """
            SELECT MIN(read_at), COUNT(*)
            FROM dsmr_consumption_electricityconsumption
            WHERE read_at >= %s AND read_at < %s
        """,
            (start, end),
        )
        dsmr_first, dsmr_count = cur.fetchone()
        dsmr_count = dsmr_count or 0

        grid_import = 0.0
        grid_export = 0.0
        sources = []

        if dsmr_count > 0:
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
            grid_import += float(row[0])
            grid_export += float(row[1])
            sources.append("DSMR")

        # Fill any gap before DSMR coverage (or full range if no DSMR) with pureenergie
        pe_end = dsmr_first if dsmr_count > 0 else end
        cur.execute(
            """
            SELECT COALESCE(SUM(grid_import), 0), COALESCE(SUM(grid_export), 0), COUNT(*)
            FROM pureenergie.consumption
            WHERE measured_at >= %s AND measured_at < %s
        """,
            (start, pe_end),
        )
        pe_import, pe_export, pe_count = cur.fetchone()
        if pe_count > 0:
            grid_import += float(pe_import)
            grid_export += float(pe_export)
            sources.append("Pure Energie")

    if not sources:
        return dict(solar_kwh=solar_kwh, grid_import=None, grid_export=None, source=None)

    return dict(
        solar_kwh=solar_kwh,
        grid_import=grid_import,
        grid_export=grid_export,
        source=" + ".join(sources),
    )


def format_label(start: datetime, end: datetime) -> str:
    def is_midnight(dt):
        return dt.hour == 0 and dt.minute == 0 and dt.second == 0

    if is_midnight(start) and is_midnight(end):
        # Full-day range
        s = start.date()
        e = (end - timedelta(seconds=1)).date()
        return s.strftime("%-d %B %Y") if s == e else f"{s} → {e}"
    else:
        same_day = start.date() == end.date()
        if same_day:
            return f"{start.strftime('%-d %B %Y')}  {start.strftime('%H:%M')} – {end.strftime('%H:%M')}"
        return f"{start.strftime('%Y-%m-%d %H:%M')} – {end.strftime('%Y-%m-%d %H:%M')}"


def pct(part, total) -> str:
    if not total:
        return "  n/a"
    return f"{part / total * 100:4.0f}%"


def bar(fraction: float, width: int = 30) -> str:
    filled = round(max(0.0, min(1.0, fraction)) * width)
    return "█" * filled + "░" * (width - filled)


def report(start: datetime, end: datetime, data: dict):
    solar = data["solar_kwh"]
    grid_import = data["grid_import"]
    grid_export = data["grid_export"]

    source_label = f"  [{data['source']}]" if data.get("source") else ""
    print(f"\nPeriod: {format_label(start, end)}{source_label}")
    print("─" * 52)

    print(f"  Solar produced       {solar:8.2f} kWh")
    if grid_export is not None:
        self_consumed = max(solar - grid_export, 0)
        print(
            f"  ├─ self-consumed     {self_consumed:8.2f} kWh  {pct(self_consumed, solar)}  {bar(self_consumed / solar if solar else 0)}"
        )
        print(f"  └─ exported to grid  {grid_export:8.2f} kWh  {pct(grid_export, solar)}")
    else:
        print("  (no grid meter data for this period)")

    print()

    if grid_import is not None:
        self_consumed = max(solar - grid_export, 0)
        total = self_consumed + grid_import
        print(f"  Total consumed       {total:8.2f} kWh")
        print(
            f"  ├─ from solar        {self_consumed:8.2f} kWh  {pct(self_consumed, total)}  {bar(self_consumed / total if total else 0)}"
        )
        print(f"  └─ from grid         {grid_import:8.2f} kWh  {pct(grid_import, total)}")
    else:
        print("  (no grid data — before Pure Energie coverage starts Jun 2022)")

    print()


def main():
    try:
        start, end = parse_period(sys.argv[1:])
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        print(__doc__, file=sys.stderr)
        sys.exit(1)

    conn = psycopg2.connect(**DB)
    data = query(conn, start, end)
    report(start, end, data)


if __name__ == "__main__":
    main()
