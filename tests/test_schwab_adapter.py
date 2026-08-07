"""Schwab adapter + auth lifecycle: parse fixtures per endpoint, dark-mode
semantics, token-file hygiene, no secrets persisted. Network-free."""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from data.adapters import schwab, schwab_auth
from data.adapters.schwab_auth import SchwabDark
from schemas.models import Document

D = Decimal
NOW = datetime(2026, 8, 7, 5, 30, tzinfo=timezone.utc)


def doc(payload: dict, tag: str, source: str, params: dict | None = None) -> Document:
    return Document(
        content_hash=f"schwab-test-{tag}", raw_bytes=json.dumps(payload).encode(),
        retrieval_url="https://api.schwabapi.com/marketdata/v1/x",
        query_params=params or {}, source_name=source,
        ingestion_time=NOW, first_eligible_decision_time=NOW)


# --------------------------------------------------------------------------- #
# Auth lifecycle (token file faked on disk)
# --------------------------------------------------------------------------- #

@pytest.fixture
def token_env(tmp_path, monkeypatch):
    path = tmp_path / "token.json"
    monkeypatch.setenv("SCHWAB_TOKEN_PATH", str(path))
    monkeypatch.setenv("SCHWAB_APP_KEY", "test-key")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "test-secret")
    return path


def _write_token(path: Path, *, refresh_hours_left: float, access_fresh: bool):
    now = datetime.now(timezone.utc)
    issued = now + timedelta(hours=refresh_hours_left) - timedelta(days=7)
    path.write_text(json.dumps({
        "access_token": "acc",
        "access_expires_at": (now + timedelta(minutes=25 if access_fresh else -1)).isoformat(),
        "refresh_token": "ref",
        "refresh_issued_at": issued.isoformat(),
        "refresh_expires_at": (issued + timedelta(days=7)).isoformat(),
    }))


def test_missing_token_file_is_dark(token_env):
    with pytest.raises(SchwabDark, match="no token file"):
        schwab_auth.access_token()


def test_hard_expired_refresh_is_dark(token_env):
    _write_token(token_env, refresh_hours_left=-1, access_fresh=True)
    with pytest.raises(SchwabDark, match="hard-expired"):
        schwab_auth.access_token()


def test_fresh_access_token_returned_without_refresh(token_env):
    _write_token(token_env, refresh_hours_left=100, access_fresh=True)
    assert schwab_auth.access_token() == "acc"


def test_rejected_refresh_is_dark(token_env, monkeypatch):
    _write_token(token_env, refresh_hours_left=100, access_fresh=False)

    class Resp:
        status_code = 400
        text = '{"error":"invalid_client"}'

    monkeypatch.setattr(schwab_auth.httpx, "post", lambda *a, **k: Resp())
    with pytest.raises(SchwabDark, match="refresh rejected"):
        schwab_auth.access_token()


def test_refresh_preserves_seven_day_clock(token_env, monkeypatch):
    _write_token(token_env, refresh_hours_left=50, access_fresh=False)
    before = schwab_auth.token_status()

    class Resp:
        status_code = 200
        text = "{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {"access_token": "acc2", "expires_in": 1800}

    monkeypatch.setattr(schwab_auth.httpx, "post", lambda *a, **k: Resp())
    assert schwab_auth.access_token() == "acc2"
    after = schwab_auth.token_status()
    assert after.refresh_expires_at == before.refresh_expires_at  # never extended
    mode = token_env.stat().st_mode & 0o777
    assert mode == 0o600


def test_missing_keys_is_dark(token_env, monkeypatch):
    monkeypatch.delenv("SCHWAB_APP_KEY")
    with pytest.raises(SchwabDark, match="not set"):
        schwab_auth.authorize_url()


# --------------------------------------------------------------------------- #
# Parsers (derive-on-read, malformed rows skipped)
# --------------------------------------------------------------------------- #

def test_parse_candles_sorted_and_skips_malformed():
    ms = lambda d: int(datetime(2026, 8, d, 4, tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
    payload = {"candles": [
        {"datetime": ms(6), "open": 2, "high": 2, "low": 2, "close": 2.5, "volume": 10},
        {"datetime": ms(5), "open": 1, "high": 1, "low": 1, "close": 1.5, "volume": 10},
        {"open": 9},                                   # malformed → skipped
    ], "symbol": "AAA"}
    candles = schwab.parse_candles(doc(payload, "candles", "schwab_history"))
    assert [c.session for c in candles] == [date(2026, 8, 5), date(2026, 8, 6)]
    assert candles[1].close == D("2.5")


def test_parse_quotes_extracts_fields_and_skips_errors_node():
    payload = {
        "SPY": {"quote": {"lastPrice": 500.25, "netPercentChange": -0.85,
                          "closePrice": 504.5, "52WeekHigh": 520.1,
                          "52WeekLow": 410.0, "totalVolume": 1000},
                "reference": {"description": "SPDR S&P 500"}},
        "$VIX": {"quote": {"lastPrice": 17.5}},
        "errors": {"invalidSymbols": ["ZZZZ"]},
    }
    quotes = schwab.parse_quotes(doc(payload, "quotes", "schwab_quotes"))
    assert set(quotes) == {"SPY", "$VIX"}
    assert quotes["SPY"].high_52wk == D("520.1")
    assert quotes["SPY"].net_pct_change == D("-0.85")
    assert quotes["$VIX"].last == D("17.5")
    assert quotes["$VIX"].high_52wk is None


def test_parse_movers_tolerates_shape_variance():
    payload = {"screeners": [
        {"symbol": "aaa", "description": "Aaa Corp", "lastPrice": 10,
         "netPercentChange": 4.2, "volume": 999},
        {"symbol": "BBB", "netChange": 1.1, "totalVolume": 5},
        {"bogus": True},
    ]}
    movers = schwab.parse_movers(doc(payload, "movers", "schwab_movers"))
    assert [m.symbol for m in movers] == ["AAA", "BBB", ""]
    assert movers[0].net_pct_change == D("4.2")
    assert movers[1].volume == 5
