"""DB-only decision-packet evidence: cache dedup + latest-per-theme lookup."""

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from data import repository as repo
from data.db import dsn
from data.migrate import migrate
from reporting import packet
from schemas.models import Document

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def test_cache_is_db_only_and_dedups_by_hash(conn):
    with conn.transaction():
        doc_id = packet.cache(conn, "# packet body", NOW,
                              theme="pkt-test-theme", commit=False)
        again = packet.cache(conn, "# packet body", NOW,
                             theme="pkt-test-theme", commit=False)
        assert again == doc_id
        doc = repo.fetch_one(conn, Document, "id = %s", (doc_id,))
        assert doc.source_name == "desk_packet"
        assert doc.retrieval_url == (
            "desk://packets/decision-packet-pkt-test-theme-2026-07-25.md")
        assert doc.extracted_text == "# packet body"
        raise psycopg.Rollback


def test_latest_doc_id_newest_per_theme_including_legacy_rows(conn):
    with conn.transaction():
        old = packet.cache(conn, "old body", NOW - timedelta(days=7),
                           theme="pkt-test-theme", commit=False)
        new = packet.cache(conn, "new body", NOW,
                           theme="pkt-test-theme", commit=False)
        other = packet.cache(conn, "other theme", NOW,
                             theme="pkt-other-theme", commit=False)
        assert old != new
        assert packet.latest_doc_id(conn, "pkt-test-theme") == new
        assert packet.latest_doc_id(conn, "pkt-other-theme") == other
        # rows cached before packets became DB-only used file:// paths — the
        # tag matcher must still find them
        legacy = Document(
            content_hash="pkt-test-legacy-hash",
            raw_bytes=b"legacy body",
            retrieval_url="file:///repo/reporting/output/"
                          "decision-packet-pkt-legacy-theme-2026-07-18.md",
            source_name="desk_packet",
            ingestion_time=NOW - timedelta(days=7),
            first_eligible_decision_time=NOW - timedelta(days=7),
        )
        legacy_id = repo.insert_document(conn, legacy)
        assert packet.latest_doc_id(conn, "pkt-legacy-theme") == legacy_id
        assert packet.latest_doc_id(conn, "pkt-no-such-theme") is None
        raise psycopg.Rollback
