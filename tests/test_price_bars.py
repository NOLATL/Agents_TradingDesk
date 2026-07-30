"""Gate B: price_bars is provably rebuildable bit-identically from the

immutable documents — never a second source of truth."""

import json
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from data import repository as repo
from data.db import dsn
from data.migrate import migrate
from data.price_bars import rebuild_price_bars, table_fingerprint
from schemas.models import Document

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def tiingo_doc(symbol: str, rows: list[dict], ingested: datetime, tag: str) -> Document:
    return Document(
        content_hash=f"pb-test-{symbol}-{tag}",
        raw_bytes=json.dumps(rows).encode(),
        retrieval_url=f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}/prices",
        source_name="tiingo",
        ingestion_time=ingested,
        first_eligible_decision_time=ingested,
    )


ROWS_V1 = [
    {"date": "2026-07-15T00:00:00.000Z", "open": 100, "high": 101, "low": 99,
     "close": 100.5, "volume": 1000, "adjOpen": 100, "adjHigh": 101, "adjLow": 99,
     "adjClose": 100.5, "adjVolume": 1000, "splitFactor": 1, "divCash": 0},
]
ROWS_V2 = ROWS_V1 + [
    {"date": "2026-07-16T00:00:00.000Z", "open": 102, "high": 103, "low": 101,
     "close": 102.5, "volume": 1100, "adjOpen": 102, "adjHigh": 103, "adjLow": 101,
     "adjClose": 102.5, "adjVolume": 1100, "splitFactor": 1, "divCash": 0},
]


def test_rebuild_is_bit_identical_and_latest_doc_wins(conn):
    with conn.transaction():
        repo.insert_document(conn, tiingo_doc("ZZTEST", ROWS_V1, NOW, "v1"))
        repo.insert_document(conn, tiingo_doc("ZZTEST", ROWS_V2, NOW + timedelta(hours=1), "v2"))

        rebuild_price_bars(conn)
        fp1 = table_fingerprint(conn)
        n1 = conn.execute("SELECT count(*) FROM price_bars WHERE symbol='ZZTEST'").fetchone()[0]
        assert n1 == 2  # latest document (v2) won wholesale

        src = conn.execute(
            "SELECT DISTINCT source_doc_hash FROM price_bars WHERE symbol='ZZTEST'"
        ).fetchall()
        assert src == [("pb-test-ZZTEST-v2",)]

        conn.execute("DELETE FROM price_bars")
        rebuild_price_bars(conn)
        assert table_fingerprint(conn) == fp1  # TRUNCATE + rebuild: bit-identical
        raise psycopg.Rollback


def test_every_bar_references_an_immutable_document(conn):
    with conn.transaction():
        repo.insert_document(conn, tiingo_doc("ZZTEST", ROWS_V1, NOW, "ref"))
        rebuild_price_bars(conn)
        n = conn.execute(
            "SELECT count(*) FROM price_bars pb LEFT JOIN documents d"
            " ON pb.source_doc_hash = d.content_hash WHERE d.id IS NULL"
        ).fetchone()[0]
        assert n == 0
        raise psycopg.Rollback

def test_scan_documents_never_enter_price_bars(conn):
    """Invariant-5 isolation (momentum scan, DECISIONS.md 2026-07-29): a
    tiingo_scan document is invisible to the rebuild — scan ETFs must never
    perturb price_bars, the restatement guard, or Gate B fingerprints."""
    with conn.transaction():
        rebuild_price_bars(conn)
        fp_before = table_fingerprint(conn)
        scan_doc = tiingo_doc("ZZSCAN", ROWS_V1, NOW, "scan")
        repo.insert_document(
            conn, scan_doc.model_copy(update={"source_name": "tiingo_scan"})
        )
        rebuild_price_bars(conn)
        assert table_fingerprint(conn) == fp_before
        n = conn.execute(
            "SELECT count(*) FROM price_bars WHERE symbol='ZZSCAN'"
        ).fetchone()[0]
        assert n == 0
        raise psycopg.Rollback
