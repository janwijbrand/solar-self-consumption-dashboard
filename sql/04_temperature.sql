-- Migration: add temperature_2m column to openmeteo.hourly
-- Run once on existing installations; safe to re-run (IF NOT EXISTS).
-- After running, re-run backfill_weather.py to populate historical values.

ALTER TABLE openmeteo.hourly
    ADD COLUMN IF NOT EXISTS temperature_2m NUMERIC(5,2);  -- °C at 2m height
