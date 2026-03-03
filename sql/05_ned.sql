-- NED (Nationaal Energie Dashboard) grid carbon intensity data.
-- Run manually once via SSH tunnel before first deploy:
--   psql -h localhost -U dsmrreader_user -d dsmrreader -f sql/05_ned.sql

CREATE SCHEMA IF NOT EXISTS ned;

-- Stores ElectricityMix (type=27) utilizations at 15-minute granularity.
-- Each row is one 15-min interval for the complete Dutch grid mix.
CREATE TABLE IF NOT EXISTS ned.utilizations (
    measured_at      TIMESTAMPTZ  NOT NULL,
    type_id          SMALLINT     NOT NULL,
    type_name        VARCHAR(50)  NOT NULL,
    volume_kwh       NUMERIC(15,3),      -- kWh generated in this interval (direct from API)
    emission_kg      NUMERIC(12,3),      -- CO₂ in kg for this interval
    emission_factor  NUMERIC(10,6),      -- kg CO₂/kWh (× 1000 = g/kWh)
    percentage       NUMERIC(8,4),       -- % of total capacity
    PRIMARY KEY (measured_at, type_id)
);

CREATE INDEX IF NOT EXISTS ned_utilizations_measured_at_desc
    ON ned.utilizations (measured_at DESC);

-- Carbon intensity per 15-min slot in gCO₂/kWh.
-- Uses emissionfactor directly (kg/kWh × 1000 = g/kWh) for type=27 (ElectricityMix),
-- with a weighted fallback for any other stored types.
CREATE OR REPLACE VIEW ned.carbon_intensity AS
SELECT
    measured_at,
    CASE WHEN SUM(volume_kwh) > 0
         THEN ROUND(SUM(emission_kg * 1000) / SUM(volume_kwh), 1)
         ELSE NULL
    END AS co2_g_per_kwh,
    SUM(volume_kwh) AS total_kwh
FROM ned.utilizations
GROUP BY measured_at;
