-- OAuth tokens for the SolarEdge Monitoring API V2 (Site Access).
--
-- V1 (api_key in the URL) was retired on 2026-11-03. V2 has no static key for
-- homeowners: the site owner approves our app once (scripts/solaredge_auth.py),
-- and the collector keeps the resulting token pair here.
--
-- The access token lasts 2 hours. Every refresh returns a new refresh token and
-- invalidates the old one, so this row must always hold the newest pair.
--
-- Run manually once before deploying the V2 collector:
--   docker exec -i dsmrdb psql -U dsmrreader_user -d dsmrreader < sql/06_solaredge_oauth.sql

CREATE TABLE IF NOT EXISTS solaredge.oauth_token (
    site_id        INTEGER      PRIMARY KEY,
    access_token   TEXT         NOT NULL,
    refresh_token  TEXT         NOT NULL,
    expires_at     TIMESTAMPTZ  NOT NULL,
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
