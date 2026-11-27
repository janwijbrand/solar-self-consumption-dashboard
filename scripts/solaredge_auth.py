#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Jan-Wijbrand Kolman
# SPDX-License-Identifier: MIT
"""
Approve this app for the SolarEdge Monitoring API V2 and store the tokens.

Run it once after setup, and again whenever the collector says the grant is gone
(for example after more than 14 days without a successful refresh).

Step 1 — print the approval link:

    python scripts/solaredge_auth.py

Open the link and sign in with the site owner's SolarEdge login (the one for
monitoring.solaredge.com and the mySolarEdge app). Approve. The browser is then sent
to the app's Redirect URI. That page may show an error; that is fine.

Step 2 — right away, pass the full URL from the browser's address bar:

    python scripts/solaredge_auth.py 'https://.../callback?code=...&site_id=...'

The code in that URL is single-use and short-lived. After storing the tokens, the
script makes one test call and prints the raw response.

Configuration: see scripts/solaredge.py, plus DB_HOST, DB_PORT, DB_NAME, DB_USER,
DB_PASSWORD.
"""

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import psycopg2
import solaredge

DB = dict(
    host=os.environ["DB_HOST"],
    port=int(os.environ.get("DB_PORT", 5432)),
    dbname=os.environ["DB_NAME"],
    user=os.environ["DB_USER"],
    password=os.environ["DB_PASSWORD"],
)


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def main():
    if not (solaredge.CLIENT_ID and solaredge.CLIENT_SECRET):
        fail("SOLAREDGE_CLIENT_ID and SOLAREDGE_CLIENT_SECRET must be set")

    if len(sys.argv) == 1:
        print("Open this link, sign in as the site owner, and approve:\n")
        print(f"  {solaredge.authorize_url()}\n")
        print("Then run this script again with the full URL from the address bar.")
        return

    query = parse_qs(urlsplit(sys.argv[1]).query)
    if "error" in query:
        fail(f"approval failed: {query['error'][0]} {query.get('error_description', [''])[0]}")
    code = query.get("code", [None])[0]
    if not code:
        fail("no 'code' parameter in that URL")
    site_id = query.get("site_id", [None])[0]
    if site_id is not None and int(site_id) != solaredge.SITE_ID:
        # Checked before the exchange, so the code isn't used up.
        fail(f"approved site {site_id}, but SOLAREDGE_SITE_ID is {solaredge.SITE_ID}")

    try:
        tokens = solaredge.exchange_code(code)
    except solaredge.SolarEdgeError as e:
        fail(str(e))

    with psycopg2.connect(**DB) as conn:
        solaredge.save_tokens(conn, tokens)
        conn.commit()
        print(f"Stored tokens for site {solaredge.SITE_ID}.")

        # One test call, so the response shape can be checked against parse_power().
        now = datetime.now(UTC)
        body = solaredge.get(
            conn,
            f"/sites/{solaredge.SITE_ID}/power",
            {
                "from": (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "resolution": "QUARTER_HOUR",
            },
        )
    print("Test call /power (last 3 hours), raw response:")
    print(json.dumps(body, indent=2)[:3000])
    rows = solaredge.parse_power(body)
    print(f"Parsed {len(rows)} values; latest: {rows[-1] if rows else None}")


if __name__ == "__main__":
    main()
