-- SolarEdge production data
-- Schema separate from DSMR reader's public schema to avoid migration conflicts.
-- Stored in the same database (dsmrreader) so grid and solar data can be joined directly.

CREATE SCHEMA IF NOT EXISTS solaredge;

CREATE TABLE IF NOT EXISTS solaredge.production (
    -- Timestamp of the 15-minute interval as reported by SolarEdge (Dutch local time → stored as UTC)
    measured_at  TIMESTAMPTZ  NOT NULL,
    -- Instantaneous power in Watts at the start of the interval
    power_w      NUMERIC(8,1) NOT NULL,

    CONSTRAINT production_pkey PRIMARY KEY (measured_at)
);

-- Most queries will be time-range scans; the primary key index covers this,
-- but an explicit DESC index speeds up "latest N rows" queries.
CREATE INDEX IF NOT EXISTS production_measured_at_desc
    ON solaredge.production (measured_at DESC);

-- Convenience view: aligns SolarEdge production with DSMR grid data.
-- DSMR electricityconsumption is recorded roughly every 10 seconds;
-- this view buckets it to 15-minute averages to match SolarEdge granularity.
CREATE OR REPLACE VIEW solaredge.combined_15min AS
SELECT
    p.measured_at,
    p.power_w                                        AS solar_w,
    avg(e.currently_delivered) * 1000                AS grid_import_w,  -- kW → W
    avg(e.currently_returned)  * 1000                AS grid_export_w,  -- kW → W
    p.power_w
        - avg(e.currently_returned)  * 1000
        + avg(e.currently_delivered) * 1000          AS consumption_w,
    p.power_w - avg(e.currently_returned) * 1000     AS self_consumed_w
FROM solaredge.production p
JOIN public.dsmr_consumption_electricityconsumption e
    ON e.read_at >= p.measured_at
   AND e.read_at <  p.measured_at + INTERVAL '15 minutes'
GROUP BY p.measured_at, p.power_w
ORDER BY p.measured_at;
