"""Cross-vendor referee (two-vendor architecture Phase B): tolerance edges,
abstention below min_overlap, raw-close basis with in-window split
exclusion, and the read-only price_bars comparison."""

from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg
import pytest

from data import reconcile
from data.db import dsn
from data.migrate import migrate

D = Decimal
NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


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


def _seed_price_bars(conn, symbol: str, sessions, closes, split_factors=None):
    from data import repository as repo
    from schemas.models import Document

    doc = Document(
        content_hash=f"ref-test-{symbol}", raw_bytes=b"{}",
        retrieval_url="u", source_name="tiingo",
        ingestion_time=NOW, first_eligible_decision_time=NOW)
    repo.insert_document(conn, doc)
    for i, s in enumerate(sessions):
        conn.execute(
            "INSERT INTO price_bars (symbol, session_date, open, high, low,"
            " close, volume, adj_open, adj_high, adj_low, adj_close,"
            " adj_volume, split_factor, source_doc_hash) VALUES"
            " (%s, %s, 1, 1, 1, %s, 1, 1, 1, 1, %s, 1, %s, %s)",
            (symbol, s, closes[i], closes[i],
             (split_factors or [1] * len(sessions))[i], f"ref-test-{symbol}"))


def test_referee_universe_raw_close_comparison(conn):
    sessions = [date(2026, 8, d) for d in (3, 4, 5)]
    with conn.transaction():
        _seed_price_bars(conn, "ZZREF", sessions, [D(100)] * 3)
        candidate = {"ZZREF": {sessions[0]: D(100), sessions[1]: D(100),
                               sessions[2]: D(102)},
                     "ZZNOBARS": {sessions[0]: D(1)}}
        mismatches, excluded = reconcile.referee_universe(
            conn, sessions[-1], candidate_closes=candidate,
            window_sessions=21, rel_tolerance=D("0.005"), min_overlap=3)
        assert excluded == []
        assert len(mismatches) == 1
        assert mismatches[0].symbol == "ZZREF"
        assert mismatches[0].session == sessions[-1]
        assert mismatches[0].candidate_close == D(102)
        raise psycopg.Rollback


def test_referee_excludes_symbols_with_in_window_split(conn):
    sessions = [date(2026, 8, d) for d in (3, 4, 5)]
    with conn.transaction():
        _seed_price_bars(conn, "ZZSPLT", sessions, [D(200), D(100), D(100)],
                         split_factors=[1, 2, 1])
        candidate = {"ZZSPLT": {s: D(100) for s in sessions}}
        mismatches, excluded = reconcile.referee_universe(
            conn, sessions[-1], candidate_closes=candidate,
            window_sessions=21, rel_tolerance=D("0.005"), min_overlap=3)
        assert mismatches == []
        assert excluded == ["ZZSPLT"]   # methodology never pollutes the check
        raise psycopg.Rollback
