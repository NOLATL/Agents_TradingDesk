"""Scout context builder (2026-07-29): evidence items carry stable citation
IDs, EDGAR trends render explicit direction labels (a DECLINED term reads as
contrary evidence, NO DATA is not citable), and recently rejected proposals
surface with their critiques. RSS/EDGAR are mocked; DB is real (scout_outcomes).
"""

from datetime import date, timedelta

import psycopg
import pytest

from data.adapters import usaspending
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

    def fake_trend(conn, terms, **kwargs):
        if kwargs.get("forms") == "8-K":
            return []  # quiet event week by default
        return [
            {"term": "reshoring", "filings_last_30d": 3, "filings_prior_30d": 5},
            {"term": "grid", "filings_last_30d": 18, "filings_prior_30d": 12,
             "filers": [{"display": "GridCo (GRID) (CIK 9)", "form": "10-Q",
                         "file_date": "2026-07-15"}]},
            {"term": "smr", "filings_last_30d": None, "filings_prior_30d": 4},
        ]

    monkeypatch.setattr(candidate_scout, "edgar_term_trend", fake_trend)
    monkeypatch.setattr(usaspending, "recent_awards", lambda conn: [])
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


def test_fred_deltas_render_with_ids_and_directions(conn, mocked_sources, monkeypatch):
    """2026-08-07 P2: FRED macro deltas render as [Fn] evidence with explicit
    directions; DECLINED joins the misread-trend map; NO DATA is not citable."""
    monkeypatch.setattr(candidate_scout.fred, "discovery_series",
                        lambda: {"HOUST": "Housing starts",
                                 "DGORDER": "Durable goods new orders",
                                 "XXX": "Never ingested"})
    monkeypatch.setattr(
        candidate_scout.fred, "series_deltas",
        lambda conn, ids: [
            {"series": "HOUST", "date": date(2026, 6, 1), "latest": 1400.0,
             "prior_3m": 1300.0, "prior_12m": 1500.0},
            {"series": "DGORDER", "date": date(2026, 6, 1), "latest": 280.0,
             "prior_3m": 300.0, "prior_12m": 290.0},
            {"series": "XXX", "date": None, "latest": None,
             "prior_3m": None, "prior_12m": None},
        ])
    with conn.transaction():
        conn.execute("DELETE FROM scout_outcomes")
        ctx = candidate_scout.build_context(conn)
        assert "FRED MACRO DELTAS" in ctx.text
        assert "[F1] Housing starts (HOUST): 1400" in ctx.text
        assert "ROSE +7.7% vs ~3m ago" in ctx.text
        assert "[F2] Durable goods new orders (DGORDER)" in ctx.text
        assert "DECLINED" in ctx.text and "contrary evidence" in ctx.text
        assert "[F3] Never ingested (XXX): NO DATA" in ctx.text
        assert {"F1", "F2"} <= ctx.issued_ids and "F3" not in ctx.issued_ids
        assert ctx.edgar_directions["F2"] == "DECLINED"
        assert "F1" in ctx.evidence_lines
        raise psycopg.Rollback


def test_coverage_balance_hint_from_idea_rows(conn, mocked_sources):
    """2026-08-07 P2: last-30d IDEA domains render as an explicit
    over/under-explored hint."""
    with conn.transaction():
        conn.execute("DELETE FROM scout_outcomes")
        conn.execute("DELETE FROM scout_attempts WHERE session_date >= %s",
                     (candidate_scout.today_et() - timedelta(days=31),))
        recent = candidate_scout.today_et() - timedelta(days=3)
        conn.execute(
            "INSERT INTO scout_attempts (session_date, attempt_no, stage,"
            " passed, issues, recorded_at) VALUES"
            " (%s, 1, 'IDEA', TRUE, 'Barge squeeze [shipping]; Drone glut"
            " [defense]; Drone parts [defense]', now())", (recent,))
        ctx = candidate_scout.build_context(conn)
        assert "DESK COVERAGE BALANCE" in ctx.text
        assert "defense x2" in ctx.text and "shipping x1" in ctx.text
        assert "under-explored domain" in ctx.text
        raise psycopg.Rollback


def test_balanced_sample_round_robins_groups():
    items = (
        [{"title": f"m{i}", "group": "macro"} for i in range(5)]
        + [{"title": "p0", "group": "pharma"}]
        + [{"title": f"d{i}", "group": "defense"} for i in range(2)]
    )
    out = candidate_scout._balanced_sample(items, limit=6)
    titles = [i["title"] for i in out]
    # one per group per round; the chatty macro feed cannot fill the window
    assert titles[:3] == ["m0", "p0", "d0"]
    assert set(titles) == {"m0", "m1", "m2", "p0", "d0", "d1"}


def test_event_scan_awards_and_registry_terms(conn, mocked_sources, monkeypatch):
    """2026-08-07 P4: ACCEPTED registry terms merge into the scan; the 8-K
    event scan renders moving terms as [Kn]; USAspending awards render as
    [Un] catalyst evidence."""
    captured = {}

    def fake_trend(conn_, terms, **kwargs):
        if kwargs.get("forms") == "8-K":
            captured["event_terms"] = list(terms)
            return [
                {"term": "grid", "filings_last_30d": 4, "filings_prior_30d": 1,
                 "filers": [{"display": "GridCo (GRID) (CIK 9)",
                             "form": "8-K", "file_date": "2026-08-05"}]},
                {"term": "smr", "filings_last_30d": 0, "filings_prior_30d": 0},
            ]
        captured["terms"] = list(terms)
        return [{"term": "grid", "filings_last_30d": 18, "filings_prior_30d": 12}]

    monkeypatch.setattr(candidate_scout, "edgar_term_trend", fake_trend)
    monkeypatch.setattr(usaspending, "recent_awards", lambda c: [
        {"recipient": "Anduril Industries", "amount": 412_000_000.0,
         "agency": "Department of Defense", "sub_agency": "Dept of the Army",
         "start_date": "2026-08-01", "description": "counter-uas systems",
         "naics": "336411"}])
    with conn.transaction():
        conn.execute("DELETE FROM scout_outcomes")
        conn.execute("DELETE FROM edgar_term_registry")
        conn.execute(
            "INSERT INTO edgar_term_registry (term, status, rationale,"
            " proposed_on, decided_on) VALUES"
            " ('data center cooling', 'ACCEPTED', 'r', '2026-08-01', '2026-08-02'),"
            " ('bad idea', 'REJECTED', 'r', '2026-08-01', '2026-08-02'),"
            " ('undecided', 'PENDING', 'r', '2026-08-01', NULL)")
        ctx = candidate_scout.build_context(conn)
        # only ACCEPTED registry terms join the scan
        assert "data center cooling" in captured["terms"]
        assert "bad idea" not in captured["terms"]
        assert "undecided" not in captured["terms"]
        assert captured["event_terms"] == captured["terms"]
        # 8-K: only the moving term renders; zero-zero weeks are dropped
        assert "EDGAR 8-K EVENT SCAN" in ctx.text
        assert '[K1] "grid": ROSE 1 -> 4' in ctx.text
        assert ctx.text.count("[K2]") == 0
        assert "K1" in ctx.issued_ids and ctx.edgar_directions["K1"] == "ROSE"
        assert ctx.edgar_filers["K1"][0]["form"] == "8-K"
        # USAspending awards render as citable [Un] evidence
        assert "FEDERAL CONTRACT AWARDS" in ctx.text
        assert ("[U1] $412M — Dept of the Army to Anduril Industries"
                " (start 2026-08-01): counter-uas systems") in ctx.text
        assert "U1" in ctx.issued_ids and "U1" in ctx.evidence_lines
        raise psycopg.Rollback


def test_award_failure_degrades_to_unavailable(conn, mocked_sources, monkeypatch):
    def boom(conn_):
        raise RuntimeError("USAspending down")

    monkeypatch.setattr(usaspending, "recent_awards", boom)
    with conn.transaction():
        conn.execute("DELETE FROM scout_outcomes")
        ctx = candidate_scout.build_context(conn)
        assert "(unavailable tonight)" in ctx.text  # enrichment, not load-bearing
        raise psycopg.Rollback
