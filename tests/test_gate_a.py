"""Gate A verification against the ACTUAL slice data in the dev DB.

These tests assert integrity properties of what the pipeline really produced
(not synthetic fixtures). They skip when no slice has been run yet, and fail
loudly if a run violated an invariant.
"""

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    n = connection.execute("SELECT count(*) FROM theses").fetchone()[0]
    if n == 0:
        pytest.skip("no slice run recorded yet — run: uv run python -m agents.run_slice")
    yield connection
    connection.close()


def test_every_source_fact_has_citation_and_document(conn):
    """Gate A: every assertion traces to a citation or a named function."""
    n = conn.execute(
        "SELECT count(*) FROM assertions WHERE type = 'SOURCE_FACT'"
        " AND (citation_span IS NULL OR citation_span = '' OR source_doc_id IS NULL)"
    ).fetchone()[0]
    assert n == 0, f"{n} SOURCE_FACT assertions lack citation or document"


def test_every_computed_has_function_and_version(conn):
    n = conn.execute(
        "SELECT count(*) FROM assertions WHERE type = 'COMPUTED'"
        " AND (function_name IS NULL OR code_version IS NULL)"
    ).fetchone()[0]
    assert n == 0, f"{n} COMPUTED assertions lack function name or code version"


def test_no_untyped_assertions_possible(conn):
    n = conn.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_name='assertions'"
        " AND column_name='type' AND is_nullable='NO'"
    ).fetchone()[0]
    assert n == 1


def test_extractions_reference_stored_hashes(conn):
    """Gate A: extractions reference stored, immutable document hashes."""
    n = conn.execute(
        "SELECT count(*) FROM extractions e LEFT JOIN documents d"
        " ON e.document_content_hash = d.content_hash WHERE d.id IS NULL"
    ).fetchone()[0]
    assert n == 0, f"{n} extractions reference hashes not in documents"


def test_no_duplicate_document_hashes(conn):
    """Gate A idempotency: immutable cache holds each response exactly once."""
    n = conn.execute(
        "SELECT count(*) FROM (SELECT content_hash FROM documents"
        " GROUP BY content_hash HAVING count(*) > 1) AS dupes"
    ).fetchone()[0]
    assert n == 0


def test_all_documents_carry_eligibility(conn):
    n = conn.execute(
        "SELECT count(*) FROM documents WHERE first_eligible_decision_time IS NULL"
        " OR ingestion_time IS NULL"
    ).fetchone()[0]
    assert n == 0


def test_agent_runs_complete_for_all_roles(conn):
    """Gate A: every LLM call logged with model tag+digest and prompt version."""
    n = conn.execute(
        "SELECT count(*) FROM agent_runs WHERE model_tag IS NULL OR prompt_version IS NULL"
        " OR model_tag NOT LIKE '%@%'"
    ).fetchone()[0]
    assert n == 0, f"{n} agent_runs missing pinned tag@digest or prompt version"
    roles = {r[0] for r in conn.execute("SELECT DISTINCT role FROM agent_runs")}
    assert {"EVIDENCE_COLLECTOR", "RESEARCH_ANALYST", "INDEPENDENT_REVIEWER",
            "REPORT_ASSEMBLER"} <= roles


def test_thesis_versions_pre_and_post_review(conn):
    """Gate A: thesis changes versioned; reviewer's pre/post both persisted."""
    n = conn.execute(
        "SELECT count(*) FROM theses t2 JOIN theses t1 ON t2.parent_version_id = t1.id"
        " WHERE t2.version = t1.version + 1"
    ).fetchone()[0]
    assert n >= 1, "no versioned pre/post-review thesis pair found"


def test_rejected_theses_in_genealogy(conn):
    n = conn.execute(
        "SELECT count(*) FROM theses t WHERE t.status = 'REJECTED' AND NOT EXISTS"
        " (SELECT 1 FROM research_genealogy g WHERE g.thesis_id = t.id AND g.rejected)"
    ).fetchone()[0]
    assert n == 0, f"{n} REJECTED theses missing from research_genealogy"


def test_forecasts_have_resolution_semantics(conn):
    """Gate A: forecasts carry criteria, horizons, and exit-type-aware fields."""
    n = conn.execute(
        "SELECT count(*) FROM forecasts WHERE resolution_criterion IS NULL"
        " OR resolution_criterion = '' OR horizon_end IS NULL"
    ).fetchone()[0]
    assert n == 0
    n = conn.execute(
        "SELECT count(*) FROM forecasts WHERE probability < 0 OR probability > 1"
    ).fetchone()[0]
    assert n == 0


def test_all_theses_carry_four_exit_types(conn):
    n = conn.execute(
        "SELECT count(*) FROM theses WHERE NOT (exit_criteria ? 'PRICE_STOP'"
        " AND exit_criteria ? 'CAUSAL_BREAK' AND exit_criteria ? 'VALUATION_EXIT'"
        " AND exit_criteria ? 'TIME_STOP')"
    ).fetchone()[0]
    assert n == 0


def test_no_analyst_visible_quarantined_assertions_misfiled(conn):
    """Every stored momentum/volatility COMPUTED assertion carries the

    quarantine flag in its payload."""
    n = conn.execute(
        "SELECT count(*) FROM assertions WHERE type = 'COMPUTED'"
        " AND (value->>'metric' LIKE 'momentum%' OR value->>'metric' LIKE 'volatility%')"
        " AND (value->>'quarantined_from_analyst')::boolean IS DISTINCT FROM TRUE"
    ).fetchone()[0]
    assert n == 0, f"{n} price-derived assertions not flagged quarantined"
