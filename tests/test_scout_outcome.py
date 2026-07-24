"""Scout transparency (migration 006): every nightly scout pass leaves one
outcome row per session, and the daily report renders it — so the morning
briefing confirms the scout ran even when review rejected the proposal
(2026-07-24, user request after the silent 2026-07-23 rejection).

LLM, review, RSS, and ntfy are mocked; the DB is real.
"""

from datetime import date

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate
from ops import daily_monitor
from reporting.daily import _scout_outcome_lines
from research import candidate_scout

SESSION = date(2001, 1, 1)  # sentinel far outside real desk history


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def outcome_row(conn):
    return conn.execute(
        "SELECT outcome, slug, title, detail FROM scout_outcomes"
        " WHERE session_date = %s", (SESSION,),
    ).fetchone()


def test_record_is_an_upsert(conn):
    with conn.transaction():
        daily_monitor.record_scout_outcome(
            conn, SESSION, "PROPOSED", "so-first", "First title")
        daily_monitor.record_scout_outcome(
            conn, SESSION, "REJECTED", "so-second", "Second title", "duplicate coverage")
        assert outcome_row(conn) == (
            "REJECTED", "so-second", "Second title", "duplicate coverage")
        raise psycopg.Rollback


def test_report_renders_rejected_scout_with_reason(conn):
    critique = "Reject. Three core problems:\n(1) duplicate coverage — thin variant"
    with conn.transaction():
        daily_monitor.record_scout_outcome(
            conn, SESSION, "REJECTED",
            "grid-interconnection-bottleneck", "Grid queue crunch", critique)
        text = "\n".join(_scout_outcome_lines(conn, SESSION))
        assert "Nightly scout: ran and proposed `grid-interconnection-bottleneck`" in text
        assert "review agent rejected it" in text
        assert "> Reject. Three core problems:" in text
        assert "> (1) duplicate coverage — thin variant" in text  # blockquote per line
        raise psycopg.Rollback


def test_report_renders_proposed_and_error(conn):
    with conn.transaction():
        daily_monitor.record_scout_outcome(
            conn, SESSION, "PROPOSED", "so-new-theme", "A fresh pattern")
        text = "\n".join(_scout_outcome_lines(conn, SESSION))
        assert "proposed `so-new-theme`" in text and "in the queue below" in text

        daily_monitor.record_scout_outcome(
            conn, SESSION, "ERROR", detail="ScoutError: no proposal artifact")
        text = "\n".join(_scout_outcome_lines(conn, SESSION))
        assert "failed before producing a candidate" in text
        assert "ScoutError: no proposal artifact" in text
        raise psycopg.Rollback


def test_stale_outcome_is_dated(conn):
    with conn.transaction():
        daily_monitor.record_scout_outcome(
            conn, SESSION, "PROPOSED", "so-old", "Old title")
        text = "\n".join(_scout_outcome_lines(conn, date(2001, 1, 3)))
        assert "last ran 2001-01-01" in text
        raise psycopg.Rollback


def test_intake_records_rejection_outcome(conn, monkeypatch):
    """The regression: a review-rejected night must leave an outcome row
    (and a notification), not vanish. intake() commits, so clean up after."""
    from data.adapters import rss as rss_adapter
    from ops import approve
    from research import theme_brief

    proposal = candidate_scout.CandidateProposal(
        slug="so-intake-rejected", title="Rejected idea",
        description="pattern", symbols=["AAA"],
        evidence_points=["one", "two"],
    )

    def rejecting_scout(_conn):
        raise candidate_scout.ReviewRejected(proposal, "single-source evidence")

    sent = []
    monkeypatch.setattr(rss_adapter, "ingest_all", lambda: None)
    monkeypatch.setattr(approve, "roll_theme_queue", lambda *a, **k: None)
    monkeypatch.setattr(candidate_scout, "scout", rejecting_scout)
    monkeypatch.setattr(theme_brief, "write_brief", lambda *a, **k: None)
    monkeypatch.setattr(daily_monitor, "notify",
                        lambda title, msg, **k: sent.append(title) or True)
    try:
        daily_monitor.intake(conn, SESSION)
        assert outcome_row(conn) == (
            "REJECTED", "so-intake-rejected", "Rejected idea", "single-source evidence")
        assert any("failed review" in t for t in sent)
    finally:
        conn.execute("DELETE FROM scout_outcomes WHERE session_date = %s", (SESSION,))
        conn.commit()
