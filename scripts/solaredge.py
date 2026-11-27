# SPDX-FileCopyrightText: 2026 Jan-Wijbrand Kolman
# SPDX-License-Identifier: MIT
"""
Shared client for the SolarEdge Monitoring API V2: OAuth tokens and site power.

V1 (api_key in the URL) was retired on 2026-11-03. V2 uses OAuth 2.0 "Site Access":
the site owner approves our app once (scripts/solaredge_auth.py) and the token pair
is kept in solaredge.oauth_token (sql/06_solaredge_oauth.sql).

Token rules, from the SolarEdge docs:
- An access token lasts 2 hours.
- Every refresh returns a new refresh token and invalidates the old one.
- A refresh token expires after 14 days without use; then the owner must approve again.

Configuration via environment variables:

    SOLAREDGE_SITE_ID        — Site ID
    SOLAREDGE_CLIENT_ID      — Client ID of the Site Access app (developer.solaredge.com)
    SOLAREDGE_CLIENT_SECRET  — Client Secret of that app
"""

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

API_BASE = "https://monitoringapi.solaredge.com/v2"
TOKEN_URL = f"{API_BASE}/oauth2/token"
AUTHORIZE_URL = "https://connect.solaredge.com/authorize"

SITE_ID = int(os.environ["SOLAREDGE_SITE_ID"])
CLIENT_ID = os.environ.get("SOLAREDGE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SOLAREDGE_CLIENT_SECRET", "")

# Refresh a little before expiry so a slow request never races the deadline.
REFRESH_MARGIN = timedelta(minutes=5)

# The docs don't state the maximum from/to range for /power. One day per call is
# conservative; a long gap just costs one credit per day of missing data.
MAX_RANGE = timedelta(days=1)

# The free tier allows 10 calls per minute (fixed clock-minute window).
CALL_SPACING_S = 6.5

_POWER_UNITS = {"W": 1.0, "KW": 1000.0, "MW": 1_000_000.0}

RE_AUTH_HINT = "run scripts/solaredge_auth.py to approve access again"


class SolarEdgeError(RuntimeError):
    pass


def authorize_url() -> str:
    """Link the site owner opens to approve this app."""
    return f"{AUTHORIZE_URL}?{urlencode({'client_id': CLIENT_ID})}"


def _token_request(payload: dict) -> dict:
    if not (CLIENT_ID and CLIENT_SECRET):
        raise SolarEdgeError("SOLAREDGE_CLIENT_ID and SOLAREDGE_CLIENT_SECRET must be set")
    resp = httpx.post(
        TOKEN_URL,
        json={**payload, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        timeout=30,
    )
    if not resp.is_success:
        # OAuth errors are deterministic (RFC 6749 body); retrying won't help.
        raise SolarEdgeError(f"token request failed: HTTP {resp.status_code} {resp.text.strip()}")
    return resp.json()


def exchange_code(code: str) -> dict:
    # No redirect_uri: authorize_url() doesn't send one, so RFC 6749 §4.1.3 doesn't
    # require it here, and SolarEdge's Authentication page omits it as well. Echoing
    # the browser's URL back could fail an exact match (browsers add a trailing "/").
    return _token_request({"grant_type": "authorization_code", "code": code})


def save_tokens(conn, tokens: dict) -> None:
    """Store a token pair. The caller commits."""
    expires_at = datetime.now(UTC) + timedelta(seconds=int(tokens["expires_in"]))
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO solaredge.oauth_token
                (site_id, access_token, refresh_token, expires_at, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (site_id) DO UPDATE SET
                access_token  = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                expires_at    = EXCLUDED.expires_at,
                updated_at    = now()
            """,
            (SITE_ID, tokens["access_token"], tokens["refresh_token"], expires_at),
        )


def access_token(conn) -> str:
    """Return a valid access token, refreshing and saving the pair when needed.

    The row lock serialises refreshes between processes (cron vs. a manual run).
    Refresh tokens are single-use, so two processes must never refresh the same one.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT access_token, refresh_token, expires_at
                FROM solaredge.oauth_token WHERE site_id = %s FOR UPDATE
                """,
                (SITE_ID,),
            )
            row = cur.fetchone()
        if row is None:
            raise SolarEdgeError(f"no SolarEdge token stored; {RE_AUTH_HINT}")
        token, refresh, expires_at = row
        if expires_at - REFRESH_MARGIN > datetime.now(UTC):
            conn.commit()  # release the row lock
            return token
        try:
            tokens = _token_request({"grant_type": "refresh_token", "refresh_token": refresh})
        except SolarEdgeError as e:
            raise SolarEdgeError(f"{e}; {RE_AUTH_HINT}") from e
        save_tokens(conn, tokens)
        conn.commit()
        return tokens["access_token"]
    except BaseException:
        conn.rollback()
        raise


def get(conn, path: str, params: dict) -> dict:
    """GET a V2 endpoint with the stored grant; raises SolarEdgeError on any failure."""
    resp = httpx.get(
        f"{API_BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {access_token(conn)}"},
        timeout=30,
    )
    if resp.status_code == 401:
        raise SolarEdgeError(f"access token rejected ({resp.text.strip()}); {RE_AUTH_HINT}")
    if resp.status_code == 429:
        # Two different 429s: the per-minute rate limit, or the monthly credit limit.
        if resp.headers.get("x-ratelimit-remaining-minute") == "0":
            raise SolarEdgeError("calls-per-minute limit reached; the next run will retry")
        raise SolarEdgeError("monthly credit limit reached; calls are blocked until the next cycle")
    if not resp.is_success:
        raise SolarEdgeError(f"HTTP {resp.status_code} for {path}: {resp.text.strip()[:500]}")
    return resp.json()


def _iso(dt: datetime) -> str:
    # V2 requires ISO 8601 with a time zone; without one it returns 400.
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_power(body: dict) -> list[tuple[datetime, float]]:
    """Parse a /sites/{id}/power response into (UTC datetime, watts) rows.

    The docs describe the envelope ({period, unit, resolution, values}) but not the
    fields inside each value. So this is strict: an unexpected shape raises instead
    of storing guesses.
    """
    unit = str(body.get("unit", "")).upper()
    if unit not in _POWER_UNITS:
        raise SolarEdgeError(f"unexpected power unit {body.get('unit')!r} in {str(body)[:300]}")
    factor = _POWER_UNITS[unit]

    rows = []
    for entry in body["values"]:
        if entry.get("value") is None:
            continue
        stamp = next((entry[k] for k in ("date", "time", "timestamp") if k in entry), None)
        if stamp is None:
            raise SolarEdgeError(f"no timestamp field in power value {entry!r}")
        # Timestamps are the site's local time with its UTC offset.
        dt = datetime.fromisoformat(stamp)
        if dt.tzinfo is None:
            raise SolarEdgeError(f"power timestamp without UTC offset: {stamp!r}")
        rows.append((dt.astimezone(UTC), float(entry["value"]) * factor))
    return rows


def fetch_power(conn, start: datetime, end: datetime) -> Iterator[list[tuple[datetime, float]]]:
    """Yield 15-minute production power rows, one batch per API call (max MAX_RANGE each)."""
    chunk_start = start
    first = True
    while chunk_start < end:
        chunk_end = min(chunk_start + MAX_RANGE, end)
        if not first:
            time.sleep(CALL_SPACING_S)
        first = False
        body = get(
            conn,
            f"/sites/{SITE_ID}/power",
            {"from": _iso(chunk_start), "to": _iso(chunk_end), "resolution": "QUARTER_HOUR"},
        )
        yield parse_power(body)
        chunk_start = chunk_end
