"""Cross-vendor referee (three-vendor architecture Phase A): tolerance edges,
abstention below min_overlap, and the read-only price_bars comparison."""

from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg
import pytest

from data import reconcile
from data.adapters.alpaca import AlpacaBar
from data.db import dsn
from data.migrate import migrate

D = Decimal
NOW = datetime(2026, 7, 30, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def _maps(*pairs):
    ref = {date(2026, 7, d): D(str(r)) for d, r, _ in pairs}
    cand = {date(2026, 7, d): D(str(c)) for d, _, c in pairs}
    return ref, cand


def test_compare_flags_only_beyond_tolerance():
    ref, cand = _maps((1, 100, 100.4), (2, 100, 100.6), (3, 100, 100))
    out = reconcile.compare_adjusted_closes(
        ref, cand, symbol="AAA", rel_tolerance=D("0.005"), min_overlap=3)
    assert [m.session for m in out] == [date(2026, 7, 2)]
    assert out[0].rel_diff == D("0.006")


def test_compare_abstains_below_min_overlap():
    ref, cand = _maps((1, 100, 200))
    assert reconcile.compare_adjusted_closes(
        ref, cand, symbol="AAA", rel_tolerance=D("0.005"), min_overlap=2) == []


def test_compare_skips_zero_reference():
    ref, cand = _maps((1, 0, 5), (2, 100, 100), (3, 100, 100))
    assert reconcile.compare_adjusted_closes(
        ref, cand, symbol="AAA", rel_tolerance=D("0.005"), min_overlap=2) == []


def test_referee_universe_reads_price_bars(conn, monkeypatch):
    with conn.transaction():
        from schemas.models import Document

        from data import repository as repo

        doc = Document(
            content_hash="ref-test-doc", raw_bytes=b"{}",
            retrieval_url="u", source_name="tiingo",
            ingestion_time=NOW, first_eligible_decision_time=NOW)
        repo.insert_document(conn, doc)
        sessions = [date(2026, 7, d) for d in (21, 22, 23)]
        for s in sessions:
            conn.execute(
                "INSERT INTO price_bars (symbol, session_date, open, high, low,"
                " close, volume, adj_open, adj_high, adj_low, adj_close,"
                " adj_volume, source_doc_hash) VALUES ('ZZREF', %s, 1, 1, 1, 1,"
                " 1, 1, 1, 1, 100, 1, 'ref-test-doc')", (s,))

        def fake_latest(conn_, adjustment):
            bars = [AlpacaBar(symbol="ZZREF", session=s, open=D(1), high=D(1),
                              low=D(1), close=D("100"), volume=1)
                    for s in sessions[:-1]]
            bars.append(AlpacaBar(symbol="ZZREF", session=sessions[-1], open=D(1),
                                  high=D(1), low=D(1), close=D("102"), volume=1))
            return {"ZZREF": bars}, sessions[-1]

        import data.adapters.alpaca as alpaca_mod
        monkeypatch.setattr(alpaca_mod, "latest_scan_bars", fake_latest)
        out = reconcile.referee_universe(
            conn, sessions[-1], symbols=["ZZREF", "ZZABSENT"],
            window_sessions=21, rel_tolerance=D("0.005"), min_overlap=3)
        assert len(out) == 1
        assert out[0].symbol == "ZZREF" and out[0].session == sessions[-1]
        assert out[0].candidate_close == D("102")
        raise psycopg.Rollback
