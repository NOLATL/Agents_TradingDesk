"""Dynamic EDGAR-term registry (2026-08-07 P4, migration 014): the local
proposer self-throttles to ~monthly, dedups against current and rejected
terms, and only HUMAN-accepted terms join the scout scan (see
test_scout_context for the merge). LLM and config are mocked; DB is real.
"""

from datetime import date, timedelta

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate
from ops.approve import ThemeGateError, decide_term
from research import term_scout

SESSION = date(2026, 8, 7)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def stub(monkeypatch, terms):
    monkeypatch.setattr(term_scout.rss, "discovery_config",
                        lambda: {"edgar_terms": ["grid interconnection"]})
    monkeypatch.setattr(term_scout.rss, "recent_headlines",
                        lambda *a, **k: ["Headline one", "Headline two"])
    monkeypatch.setattr(
        term_scout.llm, "structured_call",
        lambda *a, **k: term_scout.TermSlate(terms=[
            term_scout.TermIdea(term=t, rationale=f"because {t}")
            for t in terms]))


def clear(conn):
    conn.execute("DELETE FROM edgar_term_registry")


def test_propose_inserts_dedups_and_throttles(conn, monkeypatch):
    stub(monkeypatch, ["sodium ion battery", "grid interconnection",
                       "previously barred"])
    with conn.transaction():
        clear(conn)
        conn.execute(
            "INSERT INTO edgar_term_registry (term, status, proposed_on,"
            " decided_on) VALUES ('previously barred', 'REJECTED', %s, %s)",
            (SESSION - timedelta(days=60), SESSION - timedelta(days=59)))
        inserted = term_scout.propose_terms(conn, SESSION, experiment_id=1)
        # config term and rejected term dedup away; one PENDING row lands
        assert inserted == 1
        rows = conn.execute(
            "SELECT term, status FROM edgar_term_registry"
            " WHERE status = 'PENDING'").fetchall()
        assert rows == [("sodium ion battery", "PENDING")]
        # cooldown: a fresh proposal batch is NOT due for another ~month
        assert term_scout.propose_terms(conn, SESSION + timedelta(days=1),
                                        experiment_id=1) == 0
        assert term_scout.due(conn, SESSION + timedelta(days=26))
        raise psycopg.Rollback


def test_decide_term_accept_reject_and_unknown(conn):
    with conn.transaction():
        clear(conn)
        conn.execute(
            "INSERT INTO edgar_term_registry (term, status, proposed_on)"
            " VALUES ('sodium ion battery', 'PENDING', %s),"
            " ('attritable munitions', 'PENDING', %s)", (SESSION, SESSION))
        assert decide_term(conn, "Sodium Ion  Battery", "ACCEPTED",
                           "good filing phrase", commit=False) == "sodium ion battery"
        assert decide_term(conn, "attritable munitions", "REJECTED",
                           None, commit=False) == "attritable munitions"
        statuses = dict(conn.execute(
            "SELECT term, status FROM edgar_term_registry").fetchall())
        assert statuses == {"sodium ion battery": "ACCEPTED",
                            "attritable munitions": "REJECTED"}
        with pytest.raises(ThemeGateError):
            decide_term(conn, "sodium ion battery", "ACCEPTED", None,
                        commit=False)  # already decided — no longer PENDING
        raise psycopg.Rollback
