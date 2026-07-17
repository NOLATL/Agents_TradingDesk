"""Repository round-trip and invariant-path tests (Stage 1 persistence layer)."""

from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from data import repository as repo
from data.db import dsn
from data.migrate import migrate
from schemas.enums import ProvenanceType
from schemas.models import Assertion, Document

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    migrate(connection)  # idempotency requirement
    yield connection
    connection.close()


def _doc(h: str, eligible: datetime) -> Document:
    return Document(
        content_hash=h,
        raw_bytes=b"payload",
        retrieval_url="https://example.test/x",
        source_name="test",
        ingestion_time=NOW,
        first_eligible_decision_time=eligible,
    )


def test_document_insert_and_fetch_by_hash(conn):
    with conn.transaction():
        doc = _doc("repo-test-hash-1", NOW)
        doc_id = repo.insert_document(conn, doc)
        got = repo.document_by_hash(conn, "repo-test-hash-1")
        assert got.id == doc_id and got.raw_bytes == b"payload"
        raise psycopg.Rollback


def test_eligibility_gate_blocks_future_records(conn):
    """invariant 2: eligible_documents never returns future-eligible rows."""
    with conn.transaction():
        repo.insert_document(conn, _doc("repo-elig-past", NOW - timedelta(days=1)))
        repo.insert_document(conn, _doc("repo-elig-future", NOW + timedelta(days=1)))
        visible = {
            d.content_hash
            for d in repo.eligible_documents(
                conn, NOW, "content_hash LIKE %s", ("repo-elig-%",)
            )
        }
        assert visible == {"repo-elig-past"}
        raise psycopg.Rollback


def test_assertion_enum_cast_round_trip(conn):
    with conn.transaction():
        a = Assertion(
            type=ProvenanceType.COMPUTED,
            value={"metric": "backlog", "figure": 1.5},
            function_name="analytics.exposure.backlog",
            code_version="test",
            created_at=NOW,
        )
        repo.insert_assertion(conn, a)
        got = repo.fetch_one(conn, Assertion, "id = %s", (a.id,))
        assert got.type is ProvenanceType.COMPUTED
        assert got.value == {"metric": "backlog", "figure": 1.5}
        raise psycopg.Rollback


def test_mutating_immutable_flag_rejected_before_sql(conn):
    doc = _doc("repo-immutable", NOW)
    doc.is_raw_immutable = False
    with pytest.raises(repo.InvariantViolation):
        repo.insert_document(conn, doc)
