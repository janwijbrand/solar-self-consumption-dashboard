-- Open-Meteo irradiance data (ERA5 archive + rolling forecast)
-- Site: configured via SITE_LAT / SITE_LON environment variables (see .env.example)
-- Coverage: 2019-08-22 onwards + 1 day ahead

CREATE SCHEMA IF NOT EXISTS openmeteo;

CREATE TABLE IF NOT EXISTS openmeteo.hourly (
    measured_at              TIMESTAMPTZ  PRIMARY KEY,
    shortwave_radiation      NUMERIC(8,2),  -- GHI W/m² (best regression predictor)
    direct_normal_irradiance NUMERIC(8,2),  -- DNI W/m²
    diffuse_radiation        NUMERIC(8,2),  -- diffuse W/m²
    temperature_2m           NUMERIC(5,2)   -- °C at 2m height (for temperature derating)
);
