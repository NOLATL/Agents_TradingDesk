"""Gate C fixtures: the approval gate genuinely blocks; the staged

milestone → CAUSAL_BREAK path works end to end; cycle_log idempotency."""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from analytics.engine import clock
from data import repository as repo
from data.db import dsn
from data.migrate import migrate
from ops import live_run
from ops.approve import approve_order, kill_thesis, reject_order
from ops.milestone_monitor import propose_causal_break
from schemas.enums import (
    ExitType,
    ForecastOutcome,
    MilestoneStatus,
    OrderSide,
    OrderStatus,
    ResolutionCause,
    ThesisStatus,
)
from schemas.models import Forecast, Milestone, Order, Thesis
from tests.test_live_run import START, setup_live  # reuse the live fixture kit

D = Decimal


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
def genesis_june(monkeypatch):
    monkeypatch.setattr(live_run, "GENESIS", START)
    monkeypatch.setattr(live_run, "notify", lambda *a, **k: True)


def proposed_order(conn, exp_id, thesis_id, sessions, symbol="AAA", qty="10"):
    now = clock.session_close_utc(sessions[0])
    o = Order(
        experiment_id=exp_id, thesis_id=thesis_id, symbol=symbol,
        side=OrderSide.BUY, status=OrderStatus.PROPOSED, strategy_tag="live-test",
        proposed_qty=D(qty), proposed_at=now, created_at=now,
    )
    repo.insert(conn, o)
    return o


def test_unapproved_orders_never_fill(conn):
    """THE invariant-6 test: a PROPOSED order does not fill; after approval

    via the gate functions it fills at the next session open."""
    with conn.transaction():
        exp, sessions = setup_live(conn, [])
        thesis_id = conn.execute(
            "SELECT id FROM theses WHERE experiment_id=%s ORDER BY id DESC LIMIT 1",
            (exp.id,)).fetchone()[0]
        order = proposed_order(conn, exp.id, thesis_id, sessions)

        live_run.run(conn, sessions[10], commit=False)
        n = conn.execute(
            "SELECT count(*) FROM trades t JOIN orders o ON t.order_id=o.id"
            " WHERE o.experiment_id=%s", (exp.id,)).fetchone()[0]
        assert n == 0  # PROPOSED = invisible to the engine

        # Human approves (backdated decided_at so the fill lands in-window)
        approve_order(conn, order.id, rationale="gate test", commit=False)
        conn.execute(
            "UPDATE orders SET decided_at = %s WHERE id = %s",
            (clock.session_close_utc(sessions[10]), order.id),
        )
        live_run.run(conn, sessions[12], commit=False)
        n = conn.execute(
            "SELECT count(*) FROM trades t JOIN orders o ON t.order_id=o.id"
            " WHERE o.experiment_id=%s", (exp.id,)).fetchone()[0]
        assert n == 1  # fills only after passing the gate
        raise psycopg.Rollback


def test_rejection_logs_and_never_fills(conn):
    with conn.transaction():
        exp, sessions = setup_live(conn, [])
        thesis_id = conn.execute(
            "SELECT id FROM theses WHERE experiment_id=%s ORDER BY id DESC LIMIT 1",
            (exp.id,)).fetchone()[0]
        order = proposed_order(conn, exp.id, thesis_id, sessions)
        reject_order(conn, order.id, rationale="not at these prices", commit=False)
        live_run.run(conn, sessions[10], commit=False)
        n = conn.execute(
            "SELECT count(*) FROM trades t JOIN orders o ON t.order_id=o.id"
            " WHERE o.experiment_id=%s", (exp.id,)).fetchone()[0]
        assert n == 0
        human = conn.execute(
            "SELECT count(*) FROM assertions WHERE type='HUMAN'"
            " AND value->>'kind'='order_rejection'").fetchone()[0]
        assert human == 1
        raise psycopg.Rollback


def _activate_thesis_with_forecasts(conn, exp_id):
    thesis = Thesis(
        experiment_id=exp_id, title="gate-c causal break", strategy_tag="live-test",
        status=ThesisStatus.ACTIVE,
        causal_chain="c", exposure_basket_approach=json.dumps(
            [{"symbol": "AAA", "rank": 1, "justification": "x",
              "supporting_assertion_ids": []}]),
        valuation_check="v",
        exit_criteria={"PRICE_STOP": "x", "CAUSAL_BREAK": "x",
                       "VALUATION_EXIT": "x", "TIME_STOP": "x"},
        model_tag="fixture", prompt_version="fixture",
        created_at=datetime.now(timezone.utc),
    )
    repo.insert(conn, thesis)
    now = datetime.now(timezone.utc)
    for i in range(3):
        repo.insert(conn, Forecast(
            thesis_id=thesis.id, statement=f"f{i}", probability=0.7,
            horizon_end=date(2027, 6, 30), resolution_criterion="r",
            created_by="RESEARCH_ANALYST", model_tag="fixture",
            prompt_version="fixture", created_at=now,
        ))
    milestone = Milestone(
        thesis_id=thesis.id, description="capex stays up",
        falsification_criterion="two hyperscalers cut capex >20%",
        status=MilestoneStatus.FALSIFIED, status_changed_at=now, created_at=now,
    )
    repo.insert(conn, milestone)
    return thesis, milestone


def test_causal_break_end_to_end_approval_resolves_forecasts(conn):
    """Falsified milestone → deterministic SELL proposals → human approval is

    the confirmation event → thesis INVALIDATED + forecasts FALSE via the

    invariant-7 authority → basket liquidates at next open."""
    with conn.transaction():
        exp, sessions = setup_live(conn, [(1, "AAA", OrderSide.BUY, "10")])
        live_run.run(conn, sessions[5], commit=False)  # position exists
        thesis, milestone = _activate_thesis_with_forecasts(conn, exp.id)

        n = propose_causal_break(conn, exp.id, thesis, sessions[6],
                                 datetime.now(timezone.utc))
        assert n == 1
        cb_order = repo.fetch_one(
            conn, Order, "exit_type = 'CAUSAL_BREAK' AND status = 'PROPOSED'")
        assert cb_order.side is OrderSide.SELL and cb_order.proposed_qty == D("10.0000")

        # Idempotent: a second monitor pass proposes nothing new
        assert propose_causal_break(conn, exp.id, thesis, sessions[6],
                                    datetime.now(timezone.utc)) == 0

        approve_order(conn, cb_order.id, rationale="milestone confirmed broken", commit=False)
        conn.execute("UPDATE orders SET decided_at = %s WHERE id = %s",
                     (clock.session_close_utc(sessions[6]), cb_order.id))

        t = repo.fetch_one(conn, Thesis, "id = %s", (thesis.id,))
        assert t.status is ThesisStatus.INVALIDATED
        outcomes = conn.execute(
            "SELECT outcome, resolution_cause, brier_score FROM forecasts"
            " WHERE thesis_id = %s", (thesis.id,)).fetchall()
        assert all(o == "FALSE" and c == "CAUSAL_BREAK" for o, c, _ in outcomes)
        assert all(abs(b - 0.49) < 1e-9 for _, _, b in outcomes)  # (0.7-0)^2

        live_run.run(conn, sessions[9], commit=False)
        sells = conn.execute(
            "SELECT count(*) FROM trades WHERE side='SELL' AND exit_type='CAUSAL_BREAK'"
        ).fetchone()[0]
        assert sells == 1  # basket liquidated after (and only after) approval
        raise psycopg.Rollback


def test_causal_break_rejection_reverts_milestone(conn):
    with conn.transaction():
        exp, sessions = setup_live(conn, [(1, "AAA", OrderSide.BUY, "10")])
        live_run.run(conn, sessions[5], commit=False)
        thesis, milestone = _activate_thesis_with_forecasts(conn, exp.id)
        propose_causal_break(conn, exp.id, thesis, sessions[6],
                             datetime.now(timezone.utc))
        cb_order = repo.fetch_one(
            conn, Order, "exit_type = 'CAUSAL_BREAK' AND status = 'PROPOSED'")

        reject_order(conn, cb_order.id, rationale="LLM misread the filing", commit=False)
        m = repo.fetch_one(conn, Milestone, "id = %s", (milestone.id,))
        assert m.status is MilestoneStatus.PENDING  # reverted
        t = repo.fetch_one(conn, Thesis, "id = %s", (thesis.id,))
        assert t.status is ThesisStatus.ACTIVE      # untouched
        unresolved = conn.execute(
            "SELECT count(*) FROM forecasts WHERE thesis_id = %s AND outcome IS NULL",
            (thesis.id,)).fetchone()[0]
        assert unresolved == 3                       # Brier protected
        raise psycopg.Rollback


def test_kill_thesis_routes_to_genealogy(conn):
    with conn.transaction():
        exp, sessions = setup_live(conn, [])
        thesis, _ = _activate_thesis_with_forecasts(conn, exp.id)
        kill_thesis(conn, thesis.id, "not convinced by the evidence", commit=False)
        row = conn.execute(
            "SELECT rejected, rejection_reason FROM research_genealogy"
            " WHERE thesis_id = %s", (thesis.id,)).fetchone()
        assert row[0] is True and "approval gate" in row[1]
        raise psycopg.Rollback
