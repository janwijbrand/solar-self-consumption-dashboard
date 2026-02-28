-- Pure Energie hourly grid import/export data
-- Scraped from the Pure Energie supplier portal (previous electricity contract).
-- Coverage: 2022-06-17 to 2026-02-16

CREATE SCHEMA IF NOT EXISTS pureenergie;

CREATE TABLE IF NOT EXISTS pureenergie.consumption (
    measured_at  TIMESTAMPTZ   PRIMARY KEY,
    grid_import  NUMERIC(10,3) NOT NULL,
    grid_export  NUMERIC(10,3) NOT NULL,
    net_kwh      NUMERIC(10,3),
    cost_eur     NUMERIC(10,2)
);
