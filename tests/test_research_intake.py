"""Absorbed research intake: the unified candidate scout proposes exactly one
mandatory theme per session, the sonnet review gate blocks rejected proposals
from the queue, and every no-theme outcome besides an idempotent re-run
surfaces as an exception — never as a silent drop.

LLM and review calls are mocked; no network, no Ollama, no Anthropic.
"""

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate
from research import candidate_scout, review_gate
from schemas.enums import ThemeSource


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def proposal(slug="ri-test-theme"):
    return candidate_scout.CandidateProposal(
        slug=slug, title="Test theme",
        description="a forming pattern", symbols=["AAA", "BBB"],
        evidence_points=["[E1] EDGAR mentions doubled",
                         "[H1] three independent headlines"],
        conviction="medium",
    )


@pytest.fixture
def mock_context(monkeypatch):
    monkeypatch.setattr(
        candidate_scout, "build_context",
        lambda *a: candidate_scout.ScoutContext(
            text="CONTEXT", issued_ids={"H1", "E1"},
            edgar_directions={"E1": "ROSE"}),
    )


def _mock_scout_llm(monkeypatch, prop):
    monkeypatch.setattr(candidate_scout.llm, "structured_call",
                        lambda conn, **kw: prop)


def test_approved_proposal_enters_queue_once_per_day(conn, monkeypatch, mock_context):
    _mock_scout_llm(monkeypatch, proposal())
    monkeypatch.setattr(
        review_gate, "review",
        lambda *a, **k: review_gate.ReviewVerdict(approve=True, critique="solid"),
    )
    with conn.transaction():
        theme = candidate_scout.scout(conn)
        assert theme is not None and theme.slug == "ri-test-theme"
        assert conn.execute(
            "SELECT source, status FROM themes WHERE slug = 'ri-test-theme'"
        ).fetchone() == ("AGENT", "CANDIDATE")
        # Second pass the same day: idempotent no-op, not a second proposal.
        assert candidate_scout.scout(conn) is None
        raise psycopg.Rollback


def test_human_proposal_does_not_block_the_scout(conn, monkeypatch, mock_context):
    from ops.approve import propose_theme

    _mock_scout_llm(monkeypatch, proposal(slug="ri-after-human"))
    monkeypatch.setattr(
        review_gate, "review",
        lambda *a, **k: review_gate.ReviewVerdict(approve=True, critique="solid"),
    )
    with conn.transaction():
        propose_theme(conn, "ri-human-today", "Human idea", ["CCC"],
                      source=ThemeSource.HUMAN, commit=False)
        theme = candidate_scout.scout(conn)
        assert theme is not None and theme.slug == "ri-after-human"
        raise psycopg.Rollback


def test_rejected_proposal_never_enters_queue_and_surfaces(conn, monkeypatch, mock_context):
    _mock_scout_llm(monkeypatch, proposal(slug="ri-rejected"))
    monkeypatch.setattr(
        review_gate, "review",
        lambda *a, **k: review_gate.ReviewVerdict(
            approve=False, critique="single-source evidence"),
    )
    with conn.transaction():
        with pytest.raises(candidate_scout.ReviewRejected, match="ri-rejected"):
            candidate_scout.scout(conn)
        assert conn.execute(
            "SELECT count(*) FROM themes WHERE slug = 'ri-rejected'"
        ).fetchone()[0] == 0
        raise psycopg.Rollback


def test_duplicate_slug_fails_loudly(conn, monkeypatch, mock_context):
    from ops.approve import propose_theme

    _mock_scout_llm(monkeypatch, proposal(slug="ri-duplicate"))
    monkeypatch.setattr(
        review_gate, "review",
        lambda *a, **k: review_gate.ReviewVerdict(approve=True, critique="ok"),
    )
    with conn.transaction():
        propose_theme(conn, "ri-duplicate", "Existing theme", ["DDD"],
                      source=ThemeSource.HUMAN, commit=False)
        with pytest.raises(candidate_scout.ScoutError, match="ri-duplicate"):
            candidate_scout.scout(conn)
        raise psycopg.Rollback


def test_unusable_slug_fails_loudly(conn, monkeypatch, mock_context):
    _mock_scout_llm(monkeypatch, proposal(slug="???"))
    monkeypatch.setattr(
        review_gate, "review",
        lambda *a, **k: review_gate.ReviewVerdict(approve=True, critique="ok"),
    )
    with conn.transaction():
        with pytest.raises(candidate_scout.ScoutError, match="unusable"):
            candidate_scout.scout(conn)
        raise psycopg.Rollback


def test_clean_proposal_gets_one_identity_refinement_pass(conn, monkeypatch, mock_context):
    """A precheck-clean draft is regenerated exactly once with the tickers'
    authoritative identities before the first Sonnet review (2026-07-29:
    memory-recalled program claims were the last hallucination mode)."""
    from research import proposal_checks

    calls = []

    def fake_llm(_conn, **kw):
        calls.append(kw["user_prompt"])
        return proposal(slug="ri-refined")

    monkeypatch.setattr(candidate_scout.llm, "structured_call", fake_llm)
    monkeypatch.setattr(proposal_checks, "check_symbols", lambda *a: [])
    monkeypatch.setattr(proposal_checks.tiingo, "fill_symbol_meta",
                        lambda *a, **k: None)
    monkeypatch.setattr(
        proposal_checks, "symbol_identity_block",
        lambda *a: "\n\nSYMBOL DIRECTORY CHECK:\n- AAA = Alpha Alloys (NYSE)")
    monkeypatch.setattr(
        review_gate, "review",
        lambda *a, **k: review_gate.ReviewVerdict(approve=True, critique="ok"),
    )
    with conn.transaction():
        theme = candidate_scout.scout(conn)
        assert theme is not None and theme.slug == "ri-refined"
        assert len(calls) == 2  # draft + one identity-grounded refinement
        assert "SYMBOL DIRECTORY CHECK" not in calls[0]
        assert "Alpha Alloys" in calls[1]
        raise psycopg.Rollback


def test_generate_with_review_retries_then_fails_loudly(monkeypatch):
    attempts = []
    verdicts = iter([
        review_gate.ReviewVerdict(approve=False, critique="try again"),
        review_gate.ReviewVerdict(approve=False, critique="still bad"),
        review_gate.ReviewVerdict(approve=False, critique="no"),
    ])
    monkeypatch.setattr(review_gate, "review", lambda *a, **k: next(verdicts))

    def generate(critique):
        attempts.append(critique)
        return f"draft-{len(attempts)}"

    artifact, approved, critique = review_gate.generate_with_review(
        generate, kind="test", context="ctx"
    )
    assert not approved and critique == "no"
    assert attempts == [None, "try again", "still bad"]  # critique fed back


def test_gate_offline_fails_open_but_flagged(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    artifact, approved, critique = review_gate.generate_with_review(
        lambda c: "draft", kind="test", context="ctx"
    )
    assert artifact == "draft" and not approved
    assert "review gate offline" in critique
