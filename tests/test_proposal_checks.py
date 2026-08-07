"""Deterministic scout prechecks (2026-07-29 anti-hallucination work order):
fabricated tickers and non-US listings are caught against the Tiingo symbol
directory, evidence citations are validated against issued IDs, and a
DECLINED EDGAR term cited with rising language is flagged. DB is real
(migration 007); no network — directory rows are seeded by the tests.
"""

from datetime import date, datetime, timezone

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate
from research import proposal_checks
from research.candidate_scout import CandidateProposal

SESSION = date(2001, 1, 2)  # sentinel far outside real desk history


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def seed_directory(conn):
    now = datetime.now(timezone.utc)
    rows = [
        ("MP", "MP Materials Corp", "US rare-earth mining", "NYSE", "Stock"),
        ("LYSCF", "Lynas Rare Earths Ltd ADR", None, "OTC MKTS", "Stock"),
        ("SPY", "SPDR S&P 500 ETF", None, "NYSE ARCA", "ETF"),
    ]
    for ticker, name, desc, exchange, asset_type in rows:
        conn.execute(
            "INSERT INTO symbol_directory (ticker, name, description, exchange,"
            " asset_type, refreshed_at) VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (ticker) DO UPDATE SET exchange = EXCLUDED.exchange,"
            " asset_type = EXCLUDED.asset_type, name = EXCLUDED.name,"
            " description = EXCLUDED.description",
            (ticker, name, desc, exchange, asset_type, now),
        )


def test_symbol_checks_kill_fabricated_and_foreign(conn):
    with conn.transaction():
        seed_directory(conn)
        issues = proposal_checks.check_symbols(conn, ["MP", "VULN", "LYSCF", "SPY"])
        text = "\n".join(issues)
        assert "VULN does not exist" in text          # fabricated ticker
        assert "LYSCF" in text and "OTC" in text      # foreign ADR fails US-only rule
        assert "SPY" in text                          # ETF, not a Stock
        assert "MP " not in text                      # NYSE stock passes
        raise psycopg.Rollback


def test_symbol_checks_skip_when_directory_empty(conn):
    with conn.transaction():
        conn.execute("DELETE FROM symbol_directory")
        assert proposal_checks.check_symbols(conn, ["ZZZZZZ"]) == []
        raise psycopg.Rollback


def test_citation_checks():
    issued = {"H1", "H2", "E1"}
    # clean: two distinct IDs across points
    assert proposal_checks.check_citations(
        ["[H1] a headline pattern", "[E1] a filing trend"], issued) == []
    # nonexistent ID
    issues = proposal_checks.check_citations(["[H99] invented"], issued)
    assert any("H99" in i for i in issues)
    # uncited point + fewer than two distinct IDs
    issues = proposal_checks.check_citations(["no citation here", "[H1] ok"], issued)
    assert any("cites no evidence ID" in i for i in issues)
    assert any("at least two DISTINCT" in i for i in issues)


def test_declined_trend_with_rising_language_flagged():
    directions = {"E1": "DECLINED", "E2": "ROSE"}
    issues = proposal_checks.check_directions(
        ["[E1] reshoring mentions are surging across filers"], "", directions)
    assert len(issues) == 1 and "DECLINED" in issues[0]
    # citing a declined term AS declining is fine
    assert proposal_checks.check_directions(
        ["[E1] reshoring mentions declined, a contrary signal"], "", directions) == []
    # rising language on a term that actually rose is fine
    assert proposal_checks.check_directions(
        ["[E2] interconnection mentions are rising"], "", directions) == []


def test_precheck_records_attempt_and_composes_message(conn):
    prop = CandidateProposal(
        slug="pc-test", title="T", description="d",
        symbols=["VULN"], evidence_points=["no citation"], conviction="low")
    with conn.transaction():
        seed_directory(conn)
        conn.execute("DELETE FROM scout_attempts WHERE session_date = %s", (SESSION,))
        message = proposal_checks.precheck_proposal(
            conn, prop, {"H1", "E1"}, {}, SESSION, 1234, "test-model")
        assert message is not None
        assert "VULN" in message and "cites no evidence ID" in message
        row = conn.execute(
            "SELECT attempt_no, stage, passed, context_chars, model_tag"
            " FROM scout_attempts WHERE session_date = %s", (SESSION,),
        ).fetchone()
        assert row == (1, "PRECHECK", False, 1234, "test-model")
        raise psycopg.Rollback


def test_symbol_identity_block(conn):
    with conn.transaction():
        seed_directory(conn)
        block = proposal_checks.symbol_identity_block(conn, ["MP", "VULN"])
        assert "MP = MP Materials Corp (NYSE)" in block
        assert "US rare-earth mining" in block
        assert "VULN: NOT FOUND" in block
        raise psycopg.Rollback


def proposal(conviction="low", evidence_points=None, symbols=("MP",)):
    return CandidateProposal(
        slug="pc-test", title="T", description="pattern",
        symbols=list(symbols),
        evidence_points=evidence_points or ["[H1][E1] point", "[H2] point"],
        conviction=conviction)


def test_citation_regex_admits_new_prefixes():
    """2026-08-07 prefix registry: [Fn]/[Kn]/[Un]/[Sn] parse like [Hn]/[En];
    membership in issued_ids stays the real gate."""
    issued = {"H1", "F2", "K3", "U4", "S5"}
    issues = proposal_checks.check_citations(
        ["[H1][F2] macro point", "[K3][U4][S5] event point"], issued)
    assert issues == []
    issues = proposal_checks.check_citations(["[F9] ghost point"], issued)
    assert any("F9" in i for i in issues)


def test_conviction_calibration_advisory():
    """MEDIUM/HIGH self-labels need >= 2 distinct cited headlines; LOW is
    never flagged (2026-08-07 evidence-strength precheck)."""
    # generic evidence base: term trends only
    flagged = proposal_checks.check_conviction_calibration(
        proposal("medium", ["[E1] trend", "[E2][F1] macro"]))
    assert flagged and "MEDIUM" in flagged[0] and "relabel" in flagged[0]
    # one headline is still thin
    flagged = proposal_checks.check_conviction_calibration(
        proposal("high", ["[H1][E1] point", "[E2] trend"]))
    assert flagged
    # two distinct headlines pass
    assert proposal_checks.check_conviction_calibration(
        proposal("medium", ["[H1][E1] point", "[H2] point"])) == []
    # low conviction is always acceptable
    assert proposal_checks.check_conviction_calibration(
        proposal("low", ["[E1] trend"])) == []
