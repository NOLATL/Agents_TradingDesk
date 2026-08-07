"""Schwab auth CLI — the Sunday-evening ritual (~90 seconds) and its
health checks. Phase B, DECISIONS.md 2026-07-30.

    uv run python -m ops.schwab_auth login    # interactive browser flow
    uv run python -m ops.schwab_auth status   # hours until hard expiry

The scan job calls check_freshness() nightly: below the configured
threshold it sends ONE deduped ntfy reminder (Saturday evening for a
Sunday login); a dark vendor degrades report sections, never a cycle.
"""

import sys
import urllib.parse
from datetime import datetime, timezone

from config.loader import CONFIG
from data.adapters import schwab_auth
from data.adapters.schwab_auth import SchwabDark
from ops.alerts import notify


def login() -> None:
    url = schwab_auth.authorize_url()
    print("1. Open this URL in a browser (any machine on the tailnet):\n")
    print(f"   {url}\n")
    print("2. Log in with your schwab.com BROKERAGE credentials, accept the")
    print("   terms, select your account(s), and continue.")
    print("3. You will land on a https://127.0.0.1:8182/... page that fails")
    print("   to load — that is expected. Copy the FULL URL from the")
    print("   address bar and paste it here.\n")
    pasted = input("Paste redirect URL: ").strip()
    query = urllib.parse.urlparse(pasted).query
    code = urllib.parse.parse_qs(query).get("code", [None])[0]
    if not code:
        print("no ?code= parameter found in that URL — copy the entire URL", file=sys.stderr)
        sys.exit(1)
    schwab_auth.exchange_code(urllib.parse.unquote(code))
    status = schwab_auth.token_status()
    print(f"\nlogged in — refresh token valid until {status.refresh_expires_at:%a %Y-%m-%d %H:%M %Z}"
          f" ({status.hours_left:.0f}h). Next ritual: Sunday evening.")


def status() -> None:
    try:
        s = schwab_auth.token_status()
    except SchwabDark as e:
        print(f"DARK: {e}")
        sys.exit(1)
    print(f"refresh token issued {s.refresh_issued_at:%Y-%m-%d %H:%M %Z},"
          f" expires {s.refresh_expires_at:%a %Y-%m-%d %H:%M %Z}"
          f" ({s.hours_left:.1f}h left)")
    if s.hours_left <= 0:
        print("EXPIRED — run: uv run python -m ops.schwab_auth login")
        sys.exit(1)


def check_freshness(conn) -> None:
    """Nightly reminder hook (called from the scan job): one ntfy `high`
    per token-lifetime when the refresh token is inside the alert window.
    Dedup via a cycle_log 'event' claim keyed to the token's issue stamp —
    re-runs and later nights of the same window stay silent."""
    threshold = CONFIG.get("schwab", {}).get("reauth_alert_hours", 24)
    try:
        s = schwab_auth.token_status()
    except SchwabDark:
        return  # dark is rendered by the report; no nightly nagging
    if s.hours_left > threshold or s.hours_left <= 0:
        return
    key = f"schwab-reauth-{s.refresh_issued_at:%Y%m%d%H%M}"
    claimed = conn.execute(
        "INSERT INTO cycle_log (cycle_type, period_key, trigger, status, started_at,"
        " finished_at, phase) VALUES ('event', %s, 'scan', 'COMPLETE', %s, %s, 'alert')"
        " ON CONFLICT (cycle_type, period_key) DO NOTHING RETURNING id",
        (key, datetime.now(timezone.utc), datetime.now(timezone.utc)),
    ).fetchone()
    conn.commit()
    if claimed:
        notify(
            "Schwab re-auth needed",
            f"Refresh token expires {s.refresh_expires_at:%a %H:%M %Z}"
            f" ({s.hours_left:.0f}h). Run: uv run python -m ops.schwab_auth login"
            " (~90s). Until then Schwab report sections go dark — nothing fails.",
            priority="high", tags="key",
        )


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "login":
        login()
    elif cmd == "status":
        status()
    else:
        print("usage: python -m ops.schwab_auth [login|status]", file=sys.stderr)
        sys.exit(2)
