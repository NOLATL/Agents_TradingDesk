"""Schwab Trader API auth lifecycle (three-vendor architecture Phase B,
DECISIONS.md 2026-07-30). Honors contracts/schwab.yaml:
- token file lives OUTSIDE the repo, the vault, pg_dump, and any LLM context
  (SCHWAB_TOKEN_PATH, chmod 600); app key/secret in .env only
- access token (~30 min) auto-refreshes; the REFRESH token hard-expires
  every 7 days (Schwab rule, no workaround) — the Sunday-evening ritual is
  `uv run python -m ops.schwab_auth login`
- an expired/absent/rejected refresh token raises SchwabDark: callers render
  "Schwab dark since <date>" sections and keep COMPLETE-ing cycles; auth
  failure is never a halt
"""

import base64
import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
REFRESH_TOKEN_LIFETIME_DAYS = 7
ACCESS_REFRESH_MARGIN_SECONDS = 120


class SchwabDark(Exception):
    """No usable token: missing file, hard-expired refresh token, or Schwab
    rejected the refresh (invalid_client). Fix: ops.schwab_auth login."""


@dataclass(frozen=True)
class TokenStatus:
    refresh_issued_at: datetime
    refresh_expires_at: datetime

    @property
    def hours_left(self) -> float:
        return (self.refresh_expires_at - datetime.now(timezone.utc)).total_seconds() / 3600


def token_path() -> Path:
    return Path(os.environ.get(
        "SCHWAB_TOKEN_PATH",
        Path.home() / ".config" / "trading-desk" / "schwab_token.json"))


def redirect_uri() -> str:
    return os.environ.get("SCHWAB_REDIRECT_URI", "https://127.0.0.1:8182")


def _credentials() -> tuple[str, str]:
    key, secret = os.environ.get("SCHWAB_APP_KEY"), os.environ.get("SCHWAB_APP_SECRET")
    if not key or not secret:
        raise SchwabDark("SCHWAB_APP_KEY / SCHWAB_APP_SECRET not set in .env")
    return key, secret


def _basic_auth() -> str:
    key, secret = _credentials()
    return "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()


def _load() -> dict:
    path = token_path()
    if not path.exists():
        raise SchwabDark(f"no token file at {path} — run: uv run python -m ops.schwab_auth login")
    return json.loads(path.read_text())


def _save(tokens: dict, *, refresh_issued_at: datetime) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    payload = {
        "access_token": tokens["access_token"],
        "access_expires_at": (now + timedelta(seconds=int(tokens.get("expires_in", 1800)))).isoformat(),
        "refresh_token": tokens["refresh_token"],
        "refresh_issued_at": refresh_issued_at.isoformat(),
        "refresh_expires_at": (refresh_issued_at
                               + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS)).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2))
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600 — never world-readable


def token_status() -> TokenStatus:
    data = _load()
    return TokenStatus(
        refresh_issued_at=datetime.fromisoformat(data["refresh_issued_at"]),
        refresh_expires_at=datetime.fromisoformat(data["refresh_expires_at"]),
    )


def exchange_code(code: str) -> None:
    """Authorization-code → token exchange (the login flow's final step).
    Stamps a fresh 7-day refresh clock."""
    response = httpx.post(
        TOKEN_URL,
        headers={"Authorization": _basic_auth(),
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "authorization_code", "code": code,
              "redirect_uri": redirect_uri()},
        timeout=30.0,
    )
    response.raise_for_status()
    _save(response.json(), refresh_issued_at=datetime.now(timezone.utc))


def access_token() -> str:
    """A currently-valid access token, refreshing if within the margin.
    Raises SchwabDark when the refresh token is gone/expired/rejected —
    the 7-day clock is fixed at login and refreshing does NOT extend it."""
    data = _load()
    now = datetime.now(timezone.utc)
    if datetime.fromisoformat(data["refresh_expires_at"]) <= now:
        raise SchwabDark("refresh token hard-expired — run: uv run python -m ops.schwab_auth login")
    expires = datetime.fromisoformat(data["access_expires_at"])
    if expires - now > timedelta(seconds=ACCESS_REFRESH_MARGIN_SECONDS):
        return data["access_token"]
    response = httpx.post(
        TOKEN_URL,
        headers={"Authorization": _basic_auth(),
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": data["refresh_token"]},
        timeout=30.0,
    )
    if response.status_code in (400, 401):
        raise SchwabDark(
            f"token refresh rejected (HTTP {response.status_code}:"
            f" {response.text[:120]}) — run: uv run python -m ops.schwab_auth login")
    response.raise_for_status()
    tokens = response.json()
    tokens.setdefault("refresh_token", data["refresh_token"])
    _save(tokens, refresh_issued_at=datetime.fromisoformat(data["refresh_issued_at"]))
    return tokens["access_token"]


def authorize_url() -> str:
    key, _ = _credentials()
    return (f"{AUTH_URL}?response_type=code&client_id={key}"
            f"&redirect_uri={redirect_uri()}")
