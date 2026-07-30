"""Alpaca scan adapter: chunking, parsing, pagination, containment, and the
newest-batch document grouping. Network-free — fetch_and_cache is faked; DB
tests use the dev database and roll back."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import psycopg
import pytest

from data import repository as repo
from data.adapters import alpaca
from data.db import dsn
from data.migrate import migrate
from schemas.models import Document

NOW = datetime(2026, 7, 30, 4, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY_ID", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "test-secret")


def payload(bars_by_symbol: dict, next_page: str | None = None) -> bytes:
    return json.dumps({"bars": bars_by_symbol, "next_page_token": next_page}).encode()


def bar(day: str, close: float) -> dict:
    return {"t": f"{day}T04:00:00Z", "o": close, "h": close, "l": close,
            "c": close, "v": 1000}


def doc(raw: bytes, tag: str, *, adjustment="all", scan_session="2026-07-29") -> Document:
    return Document(
        content_hash=f"alp-test-{tag}", raw_bytes=raw,
        retrieval_url=alpaca.BARS_URL,
        query_params={"adjustment": adjustment, "scan_session": scan_session,
                      "symbols": "AAA,BBB"},
        source_name="alpaca_scan", ingestion_time=NOW,
        first_eligible_decision_time=NOW,
    )


def test_chunk_symbols_sorted_deduped_sized():
    chunks = alpaca.chunk_symbols(["b", "A", "a", "C", "d", "B"], 2)
    assert chunks == [["A", "B"], ["C", "D"]]


def test_parse_bars_skips_malformed():
    raw = payload({
        "AAA": [bar("2026-07-28", 10.5), {"t": "bad"}, bar("2026-07-29", 11.0)],
        "BBB": [{"o": 1}],  # no timestamp/close → all malformed → symbol absent
    })
    out = alpaca.parse_bars(doc(raw, "parse"))
    assert list(out) == ["AAA"]
    assert [b.session for b in out["AAA"]] == [date(2026, 7, 28), date(2026, 7, 29)]
    assert out["AAA"][1].close == Decimal("11.0")


def test_missing_keys_raise_dark(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET_KEY", raising=False)
    with pytest.raises(alpaca.AlpacaDark):
        alpaca._headers()


def test_ingest_chunk_paginates_and_scrubs_secrets(conn, keys, monkeypatch):
    calls = []

    def fake_fetch(conn_, **kwargs):
        calls.append(kwargs)
        next_page = "tok2" if len(calls) == 1 else None
        return doc(payload({"AAA": [bar("2026-07-29", 1.0)]}, next_page),
                   f"page{len(calls)}"), True

    monkeypatch.setattr(alpaca, "fetch_and_cache", fake_fetch)
    docs = alpaca.ingest_scan_chunk(
        conn, ["AAA", "BBB"], start=date(2026, 3, 21),
        session=date(2026, 7, 29), adjustment="all", feed="sip")
    assert len(docs) == 2
    assert calls[1]["params"]["page_token"] == "tok2"
    for kwargs in calls:
        stored = kwargs["stored_params"]
        assert stored["scan_session"] == "2026-07-29"
        assert "test-key" not in json.dumps(stored)      # keys live in headers only
        assert kwargs["headers"]["APCA-API-KEY-ID"] == "test-key"


def _universe_kwargs():
    return dict(session=date(2026, 7, 29), history_days=130, chunk_size=2,
                max_requests=10, adjustments=("all",))


def test_universe_auth_failure_raises_dark(conn, keys, monkeypatch):
    req = httpx.Request("GET", alpaca.BARS_URL)
    err = httpx.HTTPStatusError("x", request=req,
                                response=httpx.Response(403, request=req))

    def boom(*a, **k):
        raise err

    monkeypatch.setattr(alpaca, "ingest_scan_chunk", boom)
    with pytest.raises(alpaca.AlpacaDark):
        alpaca.ingest_scan_universe(conn, ["AAA", "BBB"], **_universe_kwargs())


def test_universe_429_gaps_chunk_and_continues(conn, keys, monkeypatch):
    req = httpx.Request("GET", alpaca.BARS_URL)

    def fake_chunk(conn_, chunk, **kwargs):
        if "AAA" in chunk:
            raise httpx.HTTPStatusError(
                "x", request=req, response=httpx.Response(429, request=req))
        return [(doc(payload({}), f"ok-{chunk[0]}"), True)]

    monkeypatch.setattr(alpaca, "ingest_scan_chunk", fake_chunk)
    fetched, hits, gaps = alpaca.ingest_scan_universe(
        conn, ["AAA", "BBB", "CCC", "DDD"], **_universe_kwargs())
    assert fetched == 1
    assert ("AAA", "SKIPPED (rate limit, all)") in gaps
    assert ("BBB", "SKIPPED (rate limit, all)") in gaps
    assert not any(s in ("CCC", "DDD") for s, _ in gaps)


def test_universe_request_cap(conn, keys, monkeypatch):
    monkeypatch.setattr(
        alpaca, "ingest_scan_chunk",
        lambda conn_, chunk, **k: [(doc(payload({}), f"cap-{chunk[0]}"), True)])
    kwargs = _universe_kwargs() | {"max_requests": 1}
    fetched, _, gaps = alpaca.ingest_scan_universe(
        conn, ["AAA", "BBB", "CCC", "DDD"], **kwargs)
    assert fetched == 1
    assert gaps == [("CCC", "SKIPPED (request cap, all)"),
                    ("DDD", "SKIPPED (request cap, all)")]


def test_latest_scan_bars_merges_pages_of_newest_batch(conn):
    with conn.transaction():
        old = doc(payload({"AAA": [bar("2026-07-25", 9.0)]}), "old",
                  scan_session="2026-07-25")
        p1 = doc(payload({"AAA": [bar("2026-07-28", 10.0)]}, "tok"), "n1")
        p2 = doc(payload({"AAA": [bar("2026-07-29", 11.0)],
                          "BBB": [bar("2026-07-29", 5.0)]}), "n2")
        raw = doc(payload({"AAA": [bar("2026-07-29", 99.0)]}), "raw",
                  adjustment="raw")
        for d in (old, p1, p2, raw):
            repo.insert_document(conn, d)
        bars, session = alpaca.latest_scan_bars(conn, adjustment="all")
        assert session == date(2026, 7, 29)
        assert [b.close for b in bars["AAA"]] == [Decimal("10.0"), Decimal("11.0")]
        assert "BBB" in bars                      # pages merged
        closes, _ = alpaca.latest_scan_closes(conn, cutoff=date(2026, 7, 28))
        assert closes["AAA"] == [Decimal("10.0")]  # cutoff truncation
        raise psycopg.Rollback
