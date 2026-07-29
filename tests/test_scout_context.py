"""Scout context builder (2026-07-29): evidence items carry stable citation
IDs, EDGAR trends render explicit direction labels (a DECLINED term reads as
contrary evidence, NO DATA is not citable), and recently rejected proposals
surface with their critiques. RSS/EDGAR are mocked; DB is real (scout_outcomes).
"""

from datetime import date, timedelta

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate
from research import candidate_scout


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def mocked_sources(monkeypatch):
    monkeypatch.setattr(
        candidate_scout.rss, "recent_items",
        lambda *a, **k: [
            {"title": "Army orders rare earth magnets", "summary": "A summary.",
             "link": "https://x.test/a", "body": "Full body text."},
            {"title": "Grid queue lengthens", "summary": None},  # old-style dict
        ],
    )
    monkeypatch.setattr(candidate_scout.rss, "discovery_config",
                        lambda: {"edgar_terms": ["reshoring", "grid", "smr"]})
    monkeypatch.setattr(
        candidate_scout, "edgar_term_trend",
        lambda conn, terms: [
            {"term": "reshoring", "filings_last_30d": 3, "filings_prior_30d": 5},
            {"term": "grid", "filings_last_30d": 18, "filings_prior_30d": 12,
             "filers": [{"display": "GridCo (GRID) (CIK 9)", "form": "10-Q",
                         "file_date": "2026-07-15"}]},
            {"term": "smr", "filings_last_30d": None, "filings_prior_30d": 4},
        ],
    )
    monkeypatch.setattr(candidate_scout, "existing_coverage",
                        lambda conn: ["grid-constraint: Grid theme"])


def test_ids_directions_and_summaries(conn, mocked_sources):
    with conn.transaction():
        conn.execute("DELETE FROM scout_outcomes")
        ctx = candidate_scout.build_context(conn)
        assert "[H1] Army orders rare earth magnets — A summary." in ctx.text
        assert "[H2] Grid queue lengthens" in ctx.text
        assert '[E1] "reshoring": DECLINED 5 -> 3' in ctx.text
        assert "contrary evidence" in ctx.text
        assert '[E2] "grid": ROSE 12 -> 18' in ctx.text
        assert '[E3] "smr": NO DATA' in ctx.text
        # NO DATA terms are not citable; everything else is
        assert ctx.issued_ids == {"H1", "H2", "E1", "E2"}
        assert ctx.edgar_directions == {"E1": "DECLINED", "E2": "ROSE"}
        # funnel raw material: per-ID items, terms, filers, rendered lines
        assert ctx.headline_items["H1"]["body"] == "Full body text."
        assert ctx.headline_items["H2"].get("link") is None  # old-style dict ok
        assert ctx.edgar_terms == {"E1": "reshoring", "E2": "grid"}
        assert ctx.edgar_filers["E2"][0]["display"] == "GridCo (GRID) (CIK 9)"
        assert ctx.edgar_filers["E1"] == []  # trend without filers key
        assert ctx.evidence_lines["H1"] == "- [H1] Army orders rare earth magnets — A summary."
        assert ctx.evidence_lines["E2"] == '- [E2] "grid": ROSE 12 -> 18'
        raise psycopg.Rollback


def test_recently_rejected_block(conn, mocked_sources):
    with conn.transaction():
        conn.execute("DELETE FROM scout_outcomes")
        recent = candidate_scout.today_et() - timedelta(days=2)
        old = candidate_scout.today_et() - timedelta(days=30)
        conn.execute(
            "INSERT INTO scout_outcomes (session_date, outcome, slug, title, detail)"
            " VALUES (%s, 'REJECTED', 'sc-rare-earth', 'Rare earth theme',"
            " 'invented mine facts'),"
            " (%s, 'REJECTED', 'sc-ancient', 'Old theme', 'stale')",
            (recent, old),
        )
        ctx = candidate_scout.build_context(conn)
        assert "RECENTLY REJECTED PROPOSALS" in ctx.text
        assert "sc-rare-earth" in ctx.text and "invented mine facts" in ctx.text
        assert "sc-ancient" not in ctx.text  # outside the 14-day window
        raise psycopg.Rollback


def test_no_rejected_block_when_clean(conn, mocked_sources):
    with conn.transaction():
        conn.execute("DELETE FROM scout_outcomes")
        ctx = candidate_scout.build_context(conn)
        assert "RECENTLY REJECTED" not in ctx.text
        raise psycopg.Rollback
