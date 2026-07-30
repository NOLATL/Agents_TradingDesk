"""Wikipedia S&P 500 constituents adapter: parsing (network-free, inline HTML
fixture) and refresh semantics (dev database, rolled back)."""

from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

from data.adapters import wikipedia
from data.db import dsn
from data.migrate import migrate
from schemas.models import Document

NOW = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


FIXTURE_HTML = """
<html><body>
<table class="wikitable"><tr><th>Symbol</th></tr><tr><td>DECOY</td></tr></table>
<table id="constituents" class="wikitable sortable">
<tbody>
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th>
<th>GICS Sub-Industry</th><th>Headquarters Location</th><th>Date added</th>
<th>CIK</th><th>Founded</th></tr>
<tr><td><a href="/wiki/3M">MMM</a></td><td>3M</td><td>Industrials</td>
<td>Industrial Conglomerates</td><td>Saint Paul, Minnesota</td>
<td>1957-03-04</td><td>0000066740</td><td>1902</td></tr>
<tr><td><a href="#">BRK.B</a><sup>[1]</sup></td><td>Berkshire Hathaway</td>
<td>Financials</td><td>Multi-Sector Holdings</td><td>Omaha, Nebraska</td>
<td>2010-02-16</td><td>0001067983</td><td>1839</td></tr>
<tr><td>BAD</td><td>Missing sector row</td><td></td>
<td></td><td></td><td></td><td></td><td></td></tr>
<tr><td>MMM</td><td>Duplicate ticker</td><td>Industrials</td>
<td>Industrial Conglomerates</td><td></td><td></td><td></td><td></td></tr>
</tbody></table>
</body></html>
"""


def doc(html: str, tag: str = "v1") -> Document:
    return Document(
        content_hash=f"wiki-test-{tag}",
        raw_bytes=html.encode(),
        retrieval_url=wikipedia.PAGE_URL,
        source_name="wikipedia_sp500",
        ingestion_time=NOW,
        first_eligible_decision_time=NOW,
    )


def test_parse_constituents_columns_and_normalization():
    rows = wikipedia.parse_constituents(doc(FIXTURE_HTML))
    assert [c.ticker for c in rows] == ["MMM", "BRK-B"]  # BRK.B → Tiingo style
    mmm = rows[0]
    assert mmm.security == "3M"
    assert mmm.gics_sector == "Industrials"
    assert mmm.gics_sub_industry == "Industrial Conglomerates"
    assert mmm.cik == "0000066740"
    assert mmm.date_added == date(1957, 3, 4)
    brk = rows[1]
    assert brk.gics_sector == "Financials"  # <sup> footnote didn't pollute cells


def test_parse_skips_malformed_and_duplicate_rows():
    rows = wikipedia.parse_constituents(doc(FIXTURE_HTML))
    tickers = [c.ticker for c in rows]
    assert "BAD" not in tickers      # empty GICS sector = absence, not repair
    assert tickers.count("MMM") == 1  # duplicate skipped


def test_parse_without_constituents_table_yields_nothing():
    assert wikipedia.parse_constituents(doc("<html><table><tr><td>1</td></tr></table></html>")) == []


def test_refresh_skips_when_fresh(conn, monkeypatch):
    with conn.transaction():
        conn.execute(
            "INSERT INTO sp500_constituents (ticker, gics_sector,"
            " snapshot_doc_hash, refreshed_at) VALUES ('ZZFRESH', 'Industrials',"
            " 'wiki-test-seed', %s)",
            (datetime.now(timezone.utc) - timedelta(days=1),),
        )

        def boom(*a, **k):
            raise AssertionError("fresh table must not trigger a fetch")

        monkeypatch.setattr(wikipedia, "ingest_snapshot", boom)
        assert wikipedia.refresh_constituents(conn) == 0
        raise psycopg.Rollback


def test_refresh_full_replace_with_provenance(conn, monkeypatch):
    """refresh commits (adapter-style), so this runs only against an empty
    table (pre-first-real-refresh) and cleans up after itself."""
    if conn.execute("SELECT 1 FROM sp500_constituents LIMIT 1").fetchone():
        pytest.skip("sp500_constituents already populated by a real refresh")
    try:
        conn.execute(
            "INSERT INTO sp500_constituents (ticker, gics_sector,"
            " snapshot_doc_hash, refreshed_at) VALUES ('ZZSTALE', 'Energy',"
            " 'wiki-test-old', %s)",
            (datetime.now(timezone.utc) - timedelta(days=30),),
        )
        conn.commit()
        monkeypatch.setattr(wikipedia, "MIN_ROWS", 2)
        monkeypatch.setattr(wikipedia, "ingest_snapshot",
                            lambda c: (doc(FIXTURE_HTML, "v2"), True))
        assert wikipedia.refresh_constituents(conn) == 2
        rows = conn.execute(
            "SELECT ticker, snapshot_doc_hash FROM sp500_constituents ORDER BY ticker"
        ).fetchall()
        assert rows == [("BRK-B", "wiki-test-v2"), ("MMM", "wiki-test-v2")]
        assert wikipedia.refresh_constituents(conn) == 0  # fresh → no-op
    finally:
        conn.execute("DELETE FROM sp500_constituents")
        conn.commit()


def test_refresh_below_floor_keeps_prior_snapshot(conn, monkeypatch):
    if conn.execute("SELECT 1 FROM sp500_constituents LIMIT 1").fetchone():
        pytest.skip("sp500_constituents already populated by a real refresh")
    try:
        conn.execute(
            "INSERT INTO sp500_constituents (ticker, gics_sector,"
            " snapshot_doc_hash, refreshed_at) VALUES ('ZZOLD', 'Utilities',"
            " 'wiki-test-old', %s)",
            (datetime.now(timezone.utc) - timedelta(days=30),),
        )
        conn.commit()
        monkeypatch.setattr(wikipedia, "ingest_snapshot",
                            lambda c: (doc(FIXTURE_HTML, "v3"), True))
        with pytest.raises(RuntimeError, match="parsed only"):
            wikipedia.refresh_constituents(conn)  # MIN_ROWS untouched (400)
        conn.rollback()
        kept = conn.execute("SELECT ticker FROM sp500_constituents").fetchall()
        assert kept == [("ZZOLD",)]
    finally:
        conn.rollback()
        conn.execute("DELETE FROM sp500_constituents")
        conn.commit()


def test_sector_map(conn):
    with conn.transaction():
        conn.execute(
            "INSERT INTO sp500_constituents (ticker, gics_sector,"
            " gics_sub_industry, snapshot_doc_hash, refreshed_at)"
            " VALUES ('ZZMAP', 'Industrials', 'Electrical Components & Equipment',"
            " 'wiki-test-map', %s)", (NOW,),
        )
        assert wikipedia.sector_map(conn)["ZZMAP"] == (
            "Industrials", "Electrical Components & Equipment")
        raise psycopg.Rollback
