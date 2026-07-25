"""reporter.narrate pipeline: per-section retry with critique, per-section
fallback, persistence shape, Sonnet-gate degradation. LLM + gate are stubbed;
persistence runs against the dev Postgres inside rollback transactions.
"""

from datetime import datetime, timezone

import psycopg
import pytest

from agents import llm
from agents.roles import reporter
from agents.state import NarrativeContext, SectionDraft
from data.db import dsn
from data.migrate import migrate
from research.review_gate import ReviewUnavailable, ReviewVerdict
from schemas.models import Thesis

# 400+ words with no digits (satisfies every section floor), one valid marker.
GOOD_TEXT = ("The basket leans on measured exposure rather than story. " * 45
             + "[^A299]")
BAD_NUMBER_TEXT = ("The company guided to $99.99 billion next year which is"
                   " simply not in the evidence. " * 20) + "[^A299]"
NO_MARKER_TEXT = "A fine paragraph with no citations at all. " * 45


def make_ctx() -> NarrativeContext:
    return NarrativeContext(
        verdict="ACTIVATE",
        causal_chain="Data center expansion drives grid modernization.",
        valuation_check="Insufficient data.",
        do_nothing_case="Backlog growth may be acquisitions.",
        challenges=["PWR backlog partially from acquisitions"],
        basket=[{"symbol": "PWR", "rank": 1, "justification": "score one",
                 "supporting_assertion_ids": [299]}],
        claims=[{"assertion_id": 299, "symbol": "PWR",
                 "claim": "Backlog was large.", "quote": "backlog",
                 "figure_name": None, "figure_value": None,
                 "figure_unit": None}],
        metrics=[], macro=[],
        forecasts=[{"statement": "PWR backlog grows", "p_prior": 0.85,
                    "p_final": 0.60, "horizon_end": "2026-12-31",
                    "resolution_criterion": "Q4 report"}],
        milestones=[], exit_criteria={"TIME_STOP": "2027-12-31"},
    )


NOW = datetime(2026, 7, 25, tzinfo=timezone.utc)
THESIS = Thesis(
    id=999_999_999, experiment_id=1, title="t", strategy_tag="test-tag",
    status="REVIEWED", causal_chain="c", exposure_basket_approach="[]",
    valuation_check="v", exit_criteria={}, model_tag="test",
    prompt_version="test", created_at=NOW,
)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _stub_llm_env(monkeypatch):
    monkeypatch.setattr(llm, "cached_digest", lambda: "stubdigest")
    monkeypatch.setattr(reporter, "_known_symbols",
                        lambda conn: {"PWR", "NOC"})


def stub_calls(monkeypatch, responder):
    calls = []

    def fake(conn, **kw):
        calls.append(kw)
        return SectionDraft(text=responder(kw["user_prompt"], len(calls)))
    monkeypatch.setattr(llm, "structured_call", fake)
    return calls


def approve_all(monkeypatch):
    monkeypatch.setattr(
        reporter.review_gate, "review",
        lambda *a, **k: ReviewVerdict(approve=True, critique="ok"))


def run_narrate(conn, **kw):
    return reporter.narrate(conn, make_ctx(), THESIS, 1, NOW,
                            commit=False, **kw)


def narrative_row(conn):
    return conn.execute(
        "SELECT value FROM assertions WHERE value->>'kind' = 'report_narrative'"
        " AND (value->>'thesis_id')::int = %s ORDER BY id DESC LIMIT 1",
        (THESIS.id,)).fetchone()


def test_narrate_persists_full_shape(conn, monkeypatch):
    stub_calls(monkeypatch, lambda prompt, i: GOOD_TEXT)
    approve_all(monkeypatch)
    with conn.transaction():
        narrative, notes = run_narrate(conn)
        assert narrative is not None and narrative.lead
        assert [c.symbol for c in narrative.company_narratives] == ["PWR"]
        value = narrative_row(conn)[0]
        assert value["audit"] == "approved"
        assert value["dropped_sections"] == []
        assert value["body_words"] > 1000
        assert value["narrative"]["thesis_narrative"].endswith("[^A299]")
        raise psycopg.Rollback


def test_violation_retry_carries_critique(conn, monkeypatch):
    seen_bad = {}

    def responder(prompt, i):
        if "INVESTMENT THESIS" in prompt and "thesis" not in seen_bad:
            seen_bad["thesis"] = True
            return BAD_NUMBER_TEXT
        if "rejected by mechanical validation" in prompt:
            seen_bad["critique_seen"] = "UNKNOWN_NUMBER" in prompt
        return GOOD_TEXT

    stub_calls(monkeypatch, responder)
    approve_all(monkeypatch)
    with conn.transaction():
        narrative, _ = run_narrate(conn)
        assert seen_bad.get("critique_seen") is True
        assert narrative.thesis_narrative == GOOD_TEXT
        raise psycopg.Rollback


def test_failing_section_dropped_others_survive(conn, monkeypatch):
    stub_calls(monkeypatch, lambda prompt, i:
               NO_MARKER_TEXT if "RISK DISCUSSION" in prompt else GOOD_TEXT)
    approve_all(monkeypatch)
    with conn.transaction():
        narrative, notes = run_narrate(conn)
        assert narrative.risk_discussion is None
        assert narrative.thesis_narrative == GOOD_TEXT
        value = narrative_row(conn)[0]
        assert "risk_discussion" in value["dropped_sections"]
        assert any("risk_discussion" in x for x in notes)
        raise psycopg.Rollback


def test_llm_failure_returns_none_and_persists_nothing(conn, monkeypatch):
    def boom(conn, **kw):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(llm, "structured_call", boom)
    with conn.transaction():
        narrative, notes = run_narrate(conn)
        assert narrative is None
        assert any("falls back to deterministic layout" in x
                   for x in notes) or any("dropped" in x for x in notes)
        assert narrative_row(conn) is None
        raise psycopg.Rollback


def test_sonnet_offline_accepted_with_note(conn, monkeypatch):
    stub_calls(monkeypatch, lambda prompt, i: GOOD_TEXT)

    def offline(*a, **k):
        raise ReviewUnavailable("no key")
    monkeypatch.setattr(reporter.review_gate, "review", offline)
    with conn.transaction():
        narrative, notes = run_narrate(conn)
        assert narrative is not None
        assert narrative_row(conn)[0]["audit"].startswith("skipped")
        assert any("Sonnet audit skipped" in x for x in notes)
        raise psycopg.Rollback


def test_unparseable_audit_never_publishes(conn, monkeypatch):
    """A truncated/unreadable verdict is NOT approval — the gate may have
    been mid-rejection. Wholesale fallback, nothing persisted."""
    stub_calls(monkeypatch, lambda prompt, i: GOOD_TEXT)

    def truncated(*a, **k):
        raise reporter.review_gate.ReviewParseError("EOF while parsing")
    monkeypatch.setattr(reporter.review_gate, "review", truncated)
    with conn.transaction():
        narrative, notes = run_narrate(conn)
        assert narrative is None
        assert narrative_row(conn) is None
        assert any("unparseable" in x for x in notes)
        raise psycopg.Rollback


def test_sonnet_reject_regenerates_only_named_section(conn, monkeypatch):
    calls = stub_calls(monkeypatch, lambda prompt, i: GOOD_TEXT)
    verdicts = iter([
        ReviewVerdict(approve=False,
                      critique="the monitoring section overstates certainty"),
        ReviewVerdict(approve=True, critique="ok"),
    ])
    monkeypatch.setattr(reporter.review_gate, "review",
                        lambda *a, **k: next(verdicts))
    with conn.transaction():
        narrative, _ = run_narrate(conn)
        assert narrative is not None
        regen = [c for c in calls
                 if "An independent audit rejected" in c["user_prompt"]]
        assert len(regen) == 1
        assert "MONITORING OUTLOOK" in regen[0]["user_prompt"]
        assert narrative_row(conn)[0]["audit"] == "approved"
        raise psycopg.Rollback
