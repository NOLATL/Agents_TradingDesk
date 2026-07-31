"""Human idea seeds (migration 011): the operator queues raw hypotheses;
the nightly scout consumes the oldest FIFO, skipping the organic funnel and
developing the seed through the unchanged draft → precheck → review path.
Seeds are CONSUMED only on terminal outcomes (PROPOSED/REJECTED); a crashed
night rolls the consumption back so the seed retries.

LLM and review calls are mocked; no network, no Ollama, no Anthropic.
"""

from datetime import date
from pathlib import Path

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate
from ops import daily_monitor
from reporting.daily import _scout_outcome_lines
from research import candidate_scout, idea_seeds, review_gate

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


def clear_todays_scout_theme(conn):
    from ops.approve import today_et

    conn.execute(
        "DELETE FROM themes WHERE source <> 'HUMAN' AND proposed_on = %s"
        " AND NOT backfilled", (today_et(),))


def clear_pending_seeds(conn):
    """Real queued seeds in the dev DB would shadow the test's FIFO head;
    clear inside the rollback transaction so nothing is committed."""
    conn.execute("DELETE FROM idea_seeds WHERE status = 'PENDING'")


def proposal(slug="is-test-theme", conviction="medium"):
    return candidate_scout.CandidateProposal(
        slug=slug, title="Seeded theme",
        description="a forming pattern", symbols=["AAA", "BBB"],
        evidence_points=["[E1] EDGAR mentions doubled",
                         "[H1] three independent headlines"],
        conviction=conviction,
    )


@pytest.fixture
def mock_context(monkeypatch):
    monkeypatch.setattr(
        candidate_scout, "build_context",
        lambda *a: candidate_scout.ScoutContext(
            text="CONTEXT", issued_ids={"H1", "E1"},
            edgar_directions={"E1": "ROSE"}),
    )


def approve_review(monkeypatch):
    monkeypatch.setattr(
        review_gate, "review",
        lambda *a, **k: review_gate.ReviewVerdict(approve=True, critique="ok"),
    )


def seed_row(conn, seed_id):
    return conn.execute(
        "SELECT status, consumed_on, outcome, theme_slug, detail"
        " FROM idea_seeds WHERE id = %s", (seed_id,),
    ).fetchone()


def test_seed_replaces_funnel_fifo_and_consumes_on_proposed(
        conn, monkeypatch, mock_context):
    def forbidden_funnel(*a, **k):
        raise AssertionError("funnel must not run on seeded nights")

    monkeypatch.setattr(candidate_scout.idea_funnel, "run_idea_funnel",
                        forbidden_funnel)
    prompts = []

    def fake_llm(_conn, **kw):
        prompts.append(kw["user_prompt"])
        return proposal()

    monkeypatch.setattr(candidate_scout.llm, "structured_call", fake_llm)
    approve_review(monkeypatch)
    with conn.transaction():
        clear_todays_scout_theme(conn)
        clear_pending_seeds(conn)
        sid_a = idea_seeds.queue_seed(conn, "AI bubble systemic risk",
                                      commit=False)
        sid_b = idea_seeds.queue_seed(conn, "second idea waits its turn",
                                      commit=False)
        theme = candidate_scout.scout(conn)
        assert theme is not None and theme.slug == "is-test-theme"
        # The drafter saw the seed as the forced winning idea.
        assert "HUMAN-SEEDED" in prompts[0]
        assert "AI bubble systemic risk" in prompts[0]
        # FIFO: A consumed with the resulting slug, B untouched.
        assert seed_row(conn, sid_a) == (
            "CONSUMED", theme.proposed_on, "PROPOSED", "is-test-theme", None)
        assert seed_row(conn, sid_b)[0] == "PENDING"
        # Instrument row marks the seeded night.
        row = conn.execute(
            "SELECT passed, issues FROM scout_attempts WHERE stage = 'SEED'"
            " ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None and row[0] is True
        assert f"seed #{sid_a}" in row[1]
        raise psycopg.Rollback


def test_seeded_theme_provenance_and_idempotency(conn, monkeypatch, mock_context):
    monkeypatch.setattr(candidate_scout.idea_funnel, "run_idea_funnel",
                        lambda *a, **k: None)
    monkeypatch.setattr(candidate_scout.llm, "structured_call",
                        lambda _conn, **kw: proposal(slug="is-provenance"))
    approve_review(monkeypatch)
    with conn.transaction():
        clear_todays_scout_theme(conn)
        clear_pending_seeds(conn)
        sid = idea_seeds.queue_seed(conn, "provenance check", commit=False)
        theme = candidate_scout.scout(conn)
        assert theme.source.value == "AGENT"  # invariant 13: agent authored it
        assert theme.source_ref == f"candidate_scout/human-seed:{sid}"
        assert theme.research_config["seeded"] is True
        assert theme.research_config["seed_id"] == sid
        assert theme.research_config["conviction"] == "medium"
        # Same-day re-run stays an idempotent no-op with a seeded proposal.
        assert candidate_scout.scout(conn) is None
        raise psycopg.Rollback


def test_rejected_seed_is_consumed_with_critique(conn, monkeypatch, mock_context):
    monkeypatch.setattr(candidate_scout.idea_funnel, "run_idea_funnel",
                        lambda *a, **k: None)
    monkeypatch.setattr(candidate_scout.llm, "structured_call",
                        lambda _conn, **kw: proposal(slug="is-rejected"))
    monkeypatch.setattr(
        review_gate, "review",
        lambda *a, **k: review_gate.ReviewVerdict(
            approve=False, critique="evidence does not support the seed"),
    )
    with conn.transaction():
        clear_todays_scout_theme(conn)
        clear_pending_seeds(conn)
        sid = idea_seeds.queue_seed(conn, "doomed idea", commit=False)
        with pytest.raises(candidate_scout.ReviewRejected) as exc:
            candidate_scout.scout(conn)
        assert exc.value.seed_id == sid
        status, _, outcome, slug, detail = seed_row(conn, sid)
        assert (status, outcome, slug) == ("CONSUMED", "REJECTED", None)
        assert "evidence does not support" in detail
        raise psycopg.Rollback


def test_error_night_leaves_seed_pending(conn, monkeypatch, mock_context):
    """The retry mechanism IS intake's rollback: a scout crash must not
    burn the seed. intake() commits, so use a committed sentinel seed and
    clean up in finally (pattern: test_scout_outcome.py)."""
    from data.adapters import rss as rss_adapter
    from data.adapters import tiingo as tiingo_adapter
    from ops import approve
    from research import theme_brief

    def exploding_llm(_conn, **kw):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(candidate_scout.idea_funnel, "run_idea_funnel",
                        lambda *a, **k: None)
    monkeypatch.setattr(candidate_scout.llm, "structured_call", exploding_llm)
    monkeypatch.setattr(rss_adapter, "ingest_all", lambda: None)
    monkeypatch.setattr(tiingo_adapter, "refresh_symbol_directory",
                        lambda *a, **k: 0)
    monkeypatch.setattr(approve, "roll_theme_queue", lambda *a, **k: None)
    monkeypatch.setattr(theme_brief, "write_brief", lambda *a, **k: None)
    monkeypatch.setattr(daily_monitor, "notify",
                        lambda title, msg, **k: True)
    clear_todays_scout_theme(conn)
    sid = idea_seeds.queue_seed(conn, "crash-night seed", commit=True)
    try:
        daily_monitor.intake(conn, SESSION)
        assert conn.execute(
            "SELECT outcome FROM scout_outcomes WHERE session_date = %s",
            (SESSION,)).fetchone()[0] == "ERROR"
        assert seed_row(conn, sid)[0] == "PENDING"  # survives for retry
    finally:
        conn.execute("DELETE FROM scout_outcomes WHERE session_date = %s",
                     (SESSION,))
        conn.execute("DELETE FROM idea_seeds WHERE id = %s", (sid,))
        conn.execute("DELETE FROM assertions WHERE type = 'HUMAN'"
                     " AND value->>'kind' = 'idea_seed_queued'"
                     " AND (value->>'seed_id')::int = %s", (sid,))
        conn.commit()


def test_no_seed_runs_organic_funnel(conn, monkeypatch, mock_context):
    funnel_calls = []
    monkeypatch.setattr(
        candidate_scout.idea_funnel, "run_idea_funnel",
        lambda *a, **k: funnel_calls.append(1) or None)
    monkeypatch.setattr(candidate_scout.llm, "structured_call",
                        lambda _conn, **kw: proposal(slug="is-organic"))
    approve_review(monkeypatch)
    with conn.transaction():
        clear_todays_scout_theme(conn)
        clear_pending_seeds(conn)
        theme = candidate_scout.scout(conn)
        assert theme is not None and theme.slug == "is-organic"
        assert funnel_calls == [1]
        assert theme.source_ref == "candidate_scout/unified"
        assert "seeded" not in theme.research_config
        raise psycopg.Rollback


def test_helpers_write_human_assertions_and_enforce_lifecycle(conn):
    with conn.transaction():
        clear_pending_seeds(conn)
        sid1 = idea_seeds.queue_seed(conn, "first in", commit=False)
        sid2 = idea_seeds.queue_seed(conn, "second in", commit=False)
        assert conn.execute(
            "SELECT human_rationale FROM assertions WHERE type = 'HUMAN'"
            " AND value->>'kind' = 'idea_seed_queued'"
            " AND (value->>'seed_id')::int = %s", (sid1,),
        ).fetchone() is not None
        # FIFO head is the older seed.
        assert idea_seeds.oldest_pending(conn) == (sid1, "first in")
        idea_seeds.cancel_seed(conn, sid1, "changed my mind", commit=False)
        assert seed_row(conn, sid1)[0] == "CANCELLED"
        assert conn.execute(
            "SELECT human_rationale FROM assertions WHERE type = 'HUMAN'"
            " AND value->>'kind' = 'idea_seed_cancelled'"
            " AND (value->>'seed_id')::int = %s", (sid1,),
        ).fetchone() == ("changed my mind",)
        assert idea_seeds.oldest_pending(conn) == (sid2, "second in")
        # Cancel is PENDING-only: a consumed seed refuses.
        idea_seeds.mark_consumed(conn, sid2, SESSION, "PROPOSED",
                                 theme_slug="is-x")
        with pytest.raises(ValueError, match="not PENDING"):
            idea_seeds.cancel_seed(conn, sid2, "too late", commit=False)
        # Empty queue → None.
        assert idea_seeds.oldest_pending(conn) is None
        # Empty seed text refuses.
        with pytest.raises(ValueError, match="empty"):
            idea_seeds.queue_seed(conn, "   ", commit=False)
        raise psycopg.Rollback


def test_cli_parser_accepts_idea_commands():
    from ops.approve import _build_parser

    p = _build_parser()
    args = p.parse_args(["idea", "add", "my hypothesis"])
    assert (args.command, args.idea_command, args.text) == (
        "idea", "add", "my hypothesis")
    args = p.parse_args(["idea", "list"])
    assert args.idea_command == "list"
    args = p.parse_args(["idea", "drop", "7", "--rationale", "stale"])
    assert (args.seed_id, args.rationale) == (7, "stale")


def test_report_labels_seeded_nights(conn):
    with conn.transaction():
        conn.execute("DELETE FROM scout_outcomes WHERE session_date <> %s",
                     (SESSION,))
        conn.execute("DELETE FROM idea_seeds WHERE consumed_on = %s",
                     (SESSION,))
        sid = idea_seeds.queue_seed(conn, "seeded night render check",
                                    commit=False)
        idea_seeds.mark_consumed(conn, sid, SESSION, "PROPOSED",
                                 theme_slug="is-render")
        daily_monitor.record_scout_outcome(
            conn, SESSION, "PROPOSED", "is-render", "Rendered title")
        text = "\n".join(_scout_outcome_lines(conn, SESSION))
        assert f"developed from your queued idea #{sid}" in text
        assert "seeded night render check" in text
        # REJECTED nights carry the label too.
        daily_monitor.record_scout_outcome(
            conn, SESSION, "REJECTED", "is-render", "Rendered title", "weak")
        text = "\n".join(_scout_outcome_lines(conn, SESSION))
        assert f"developed from your queued idea #{sid}" in text
        raise psycopg.Rollback


def test_migration_011_is_idempotent(conn):
    sql = Path(__file__).resolve().parents[1].joinpath(
        "schemas", "migrations", "011_idea_seeds.sql").read_text()
    with conn.transaction():
        conn.execute(sql)  # second application on an already-migrated DB
        raise psycopg.Rollback
