"""Adapter tests: eligibility derivation, idempotent caching, parsing,

reconciliation classification. Network-free — HTTP is faked; DB tests use the
dev database and roll back.
"""

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import psycopg
import pytest

from data import repository as repo
from data.adapters import core
from data.adapters.edgar import filing_eligibility
from data.adapters.tiingo import parse_bars
from data.db import dsn
from data.migrate import migrate
from data.reconcile import classify_price_discrepancy, raise_incident
from schemas.enums import IncidentClass
from schemas.models import Document

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


# --------------------------------------------------------------------------- #
# Eligibility derivation (contract session-date rules, exchange_calendars)
# --------------------------------------------------------------------------- #

def test_next_session_open_weekend():
    # Saturday 2026-07-18 → Monday 2026-07-20 09:30 ET open
    saturday = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    open_dt = core.next_session_open(saturday)
    assert open_dt.astimezone(ET).replace(tzinfo=None) == datetime(2026, 7, 20, 9, 30)


def test_next_session_open_holiday():
    # July 3 2026 is the observed Independence Day holiday (July 4 = Saturday);
    # a timestamp on Thursday evening July 2 must map to Monday July 6.
    thursday_evening = datetime(2026, 7, 2, 23, 0, tzinfo=UTC)
    open_dt = core.next_session_open(thursday_evening)
    assert open_dt.astimezone(ET).replace(tzinfo=None) == datetime(2026, 7, 6, 9, 30)


def test_filing_after_cutoff_slips_a_day():
    # Accepted Tue 2026-07-14 18:01 ET (after 17:30) → treated as Wed →
    # eligible at Thursday's open? No: effective Wed morning maps to Wed's
    # open if before 09:30... effective time = Wed 18:01? The rule shifts the
    # date by one day keeping the time, so Wed 18:01 ET → next open Thu 09:30.
    accepted = datetime(2026, 7, 14, 18, 1, tzinfo=ET)
    open_dt = filing_eligibility(accepted)
    assert open_dt.astimezone(ET).replace(tzinfo=None) == datetime(2026, 7, 16, 9, 30)


def test_filing_before_cutoff_next_open():
    # Accepted Tue 2026-07-14 12:00 ET → eligible Wed 09:30 ET open.
    accepted = datetime(2026, 7, 14, 12, 0, tzinfo=ET)
    open_dt = filing_eligibility(accepted)
    assert open_dt.astimezone(ET).replace(tzinfo=None) == datetime(2026, 7, 15, 9, 30)


# --------------------------------------------------------------------------- #
# Immutable caching + idempotency (fetch faked)
# --------------------------------------------------------------------------- #

class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


def test_fetch_and_cache_idempotent(conn, monkeypatch):
    calls = {"n": 0}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResponse(b"same-bytes-every-time")

    monkeypatch.setattr(core.httpx, "get", fake_get)
    with conn.transaction():
        doc1, fetched1 = core.fetch_and_cache(
            conn, url="https://x.test/a", params=None,
            source_name="test", license_note="t",
        )
        doc2, fetched2 = core.fetch_and_cache(
            conn, url="https://x.test/a", params=None,
            source_name="test", license_note="t",
        )
        assert fetched1 is True and fetched2 is False
        assert doc1.id == doc2.id  # same row, no duplicate
        n_rows = conn.execute(
            "SELECT count(*) FROM documents WHERE content_hash = %s",
            (doc1.content_hash,),
        ).fetchone()[0]
        assert n_rows == 1
        raise psycopg.Rollback


def test_secret_params_never_stored(conn, monkeypatch):
    monkeypatch.setattr(core.httpx, "get", lambda *a, **k: FakeResponse(b"fred-payload"))
    with conn.transaction():
        doc, _ = core.fetch_and_cache(
            conn, url="https://api.test/obs",
            params={"series_id": "IPUTIL", "api_key": "SECRET"},
            stored_params={"series_id": "IPUTIL"},
            source_name="fred", license_note="t",
        )
        assert "api_key" not in doc.query_params
        stored = conn.execute(
            "SELECT query_params::text FROM documents WHERE id = %s", (doc.id,)
        ).fetchone()[0]
        assert "SECRET" not in stored
        raise psycopg.Rollback


# --------------------------------------------------------------------------- #
# Parsing (derive-on-read)
# --------------------------------------------------------------------------- #

def _json_doc(payload: str) -> Document:
    now = datetime(2026, 7, 17, tzinfo=UTC)
    return Document(
        content_hash="x", raw_bytes=payload.encode(), retrieval_url="u",
        source_name="tiingo", ingestion_time=now, first_eligible_decision_time=now,
    )


def test_tiingo_parse_skips_malformed_rows():
    payload = """[
      {"date":"2026-07-15T00:00:00.000Z","open":100,"high":101,"low":99,"close":100.5,
       "volume":1000,"adjClose":100.5,"splitFactor":1,"divCash":0},
      {"date":"2026-07-16T00:00:00.000Z","open":null,"high":null,"low":null,"close":null,
       "volume":null,"adjClose":null,"splitFactor":1,"divCash":0},
      {"date":"2026-07-17T00:00:00.000Z","open":101,"high":102,"low":100,"close":101.5,
       "volume":2000,"adjClose":101.5,"splitFactor":1,"divCash":0}
    ]"""
    bars = parse_bars(_json_doc(payload), "ETN")
    assert [b.session.day for b in bars] == [15, 17]  # null row = absence, not fill
    assert bars[0].close == Decimal("100.5")
    assert bars[1].split_factor == Decimal("1")


# --------------------------------------------------------------------------- #
# Reconciliation classification
# --------------------------------------------------------------------------- #

def test_discrepancy_classification():
    assert classify_price_discrepancy(Decimal("100"), Decimal("50")) is IncidentClass.SPLIT
    assert classify_price_discrepancy(Decimal("100"), Decimal("99.1")) is IncidentClass.DIVIDEND
    assert classify_price_discrepancy(Decimal("100"), Decimal("7")) is IncidentClass.BAD_PRINT
    assert classify_price_discrepancy(Decimal("0"), Decimal("7")) is IncidentClass.BAD_PRINT


def test_raise_incident_writes_classified_row(conn):
    with conn.transaction():
        incident = raise_incident(
            conn,
            incident_class=IncidentClass.SPLIT,
            source_name="stooq", symbol="ETN",
            description="historical close halved between fetches",
        )
        assert incident.id is not None
        raise psycopg.Rollback
