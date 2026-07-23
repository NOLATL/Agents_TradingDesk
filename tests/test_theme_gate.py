"""Unified-pipeline decision gates (consolidation spec, migration 004):

- one accepted theme per day, across all sources — friendly check + DB index
- rejected themes DEFER and resurface next Monday, never discarded
- a rejected thesis fully closes its thread (theme CLOSED, no auto-requeue)
- `dead` is the only human path that retires a live thesis; unwind proposals
  stop at the order gate, and forecasts resolve UNRESOLVED, never TRUE/FALSE
"""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from data import repository as repo
from data.db import dsn
from data.migrate import migrate
from ops import approve
from schemas.enums import (
    ExitType,
    OrderSide,
    OrderStatus,
    ThemeStatus,
    ThesisStatus,
)
from schemas.models import Experiment, Forecast, Order, Theme, Thesis, Trade

D = Decimal
NOW = datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)
EXIT_CRITERIA = {t.value: "x" for t in ExitType}


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
def _open_todays_slot(conn):
    """The live dev DB may genuinely hold today's accepted theme; gate tests
    need the slot open. Wrap every test in an outer rollback transaction
    (inner test transactions become savepoints) and clear the slot inside it
    — nothing here ever commits."""
    with conn.transaction(force_rollback=True):
        conn.execute(
            "UPDATE themes SET accepted_on = NULL WHERE accepted_on = %s",
            (approve.today_et(),),
        )
        yield


def propose(conn, slug, symbols=("AAA", "BBB")):
    return approve.propose_theme(
        conn, slug, f"test theme {slug}", list(symbols), commit=False
    )


def make_thesis(conn, theme: Theme, status=ThesisStatus.REVIEWED,
                basket="[]") -> Thesis:
    exp = repo.fetch_one(conn, Experiment, "name = 'EXP-001'")
    thesis = Thesis(
        experiment_id=exp.id, title=f"thesis for {theme.slug}",
        strategy_tag=theme.slug, theme_id=theme.id, status=status,
        causal_chain="A → B → C", exposure_basket_approach=basket,
        valuation_check="v", exit_criteria=EXIT_CRITERIA,
        model_tag="test", prompt_version="test", created_at=NOW,
    )
    repo.insert(conn, thesis)
    return thesis


# --------------------------------------------------------------------------- #
# Theme intake
# --------------------------------------------------------------------------- #

def test_one_accept_per_day_across_sources(conn):
    with conn.transaction():
        propose(conn, "tg-alpha")
        propose(conn, "tg-beta")
        accepted = approve.accept_theme(conn, "tg-alpha", commit=False)
        assert accepted.status is ThemeStatus.ACCEPTED
        assert accepted.accepted_on == approve.today_et()
        with pytest.raises(approve.ThemeGateError, match="slot is already used"):
            approve.accept_theme(conn, "tg-beta", commit=False)
        raise psycopg.Rollback


def test_cap_is_db_enforced_even_past_the_friendly_check(conn):
    with conn.transaction():
        propose(conn, "tg-gamma")
        propose(conn, "tg-delta")
        approve.accept_theme(conn, "tg-gamma", commit=False)
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute(
                    "UPDATE themes SET status = 'ACCEPTED', accepted_on = %s"
                    " WHERE slug = 'tg-delta'", (approve.today_et(),),
                )
        raise psycopg.Rollback


def test_accept_activates_symbols_next_session(conn):
    with conn.transaction():
        propose(conn, "tg-active", symbols=("XYZ",))
        approve.accept_theme(conn, "tg-active", commit=False)
        row = conn.execute(
            "SELECT ts.activation_session FROM theme_symbols ts"
            " JOIN themes t ON ts.theme_id = t.id WHERE t.slug = 'tg-active'"
        ).fetchone()
        assert row[0] > approve.today_et() - timedelta(days=1)
        assert row[0] > date(2026, 7, 20)  # strictly after genesis backfill
        raise psycopg.Rollback


def test_reject_is_terminal(conn):
    with conn.transaction():
        propose(conn, "tg-nope")
        closed = approve.reject_theme(conn, "tg-nope", "not a real theme", commit=False)
        assert closed.status is ThemeStatus.CLOSED
        assert closed.closed_reason == "user_rejected"
        with pytest.raises(approve.ThemeGateError, match="not decidable"):
            approve.accept_theme(conn, "tg-nope", commit=False)
        raise psycopg.Rollback


def test_undecided_candidates_defer_and_resurface_monday(conn):
    from datetime import timedelta

    with conn.transaction():
        theme = propose(conn, "tg-undecided")
        tomorrow = approve.today_et() + timedelta(days=1)
        _, deferred = approve.roll_theme_queue(conn, tomorrow, commit=False)
        assert deferred >= 1
        row = approve._theme(conn, "tg-undecided")
        assert row.status is ThemeStatus.DEFERRED
        assert row.rolled_forward_count == 1
        assert row.resurface_on.weekday() == 0        # a Monday
        assert row.resurface_on > tomorrow            # strictly forward

        monday = row.resurface_on
        resurfaced, _ = approve.roll_theme_queue(conn, monday, commit=False)
        assert resurfaced >= 1
        row = approve._theme(conn, "tg-undecided")
        assert row.status is ThemeStatus.CANDIDATE
        assert row.queued_on == monday  # fresh queue entry stamps the resurface day
        # A just-resurfaced theme is NOT re-deferred by the same session's roll.
        approve.roll_theme_queue(conn, monday, commit=False)
        assert approve._theme(conn, "tg-undecided").status is ThemeStatus.CANDIDATE
        raise psycopg.Rollback


def test_fresh_candidates_survive_the_nightly_roll(conn):
    with conn.transaction():
        propose(conn, "tg-fresh")  # queued today
        approve.roll_theme_queue(conn, approve.today_et(), commit=False)
        assert approve._theme(conn, "tg-fresh").status is ThemeStatus.CANDIDATE
        raise psycopg.Rollback


def test_deferred_theme_can_be_accepted_directly(conn):
    from datetime import timedelta

    with conn.transaction():
        propose(conn, "tg-comeback")
        approve.roll_theme_queue(
            conn, approve.today_et() + timedelta(days=1), commit=False
        )
        theme = approve.accept_theme(conn, "tg-comeback", commit=False)
        assert theme.status is ThemeStatus.ACCEPTED
        raise psycopg.Rollback


# --------------------------------------------------------------------------- #
# Thesis gate
# --------------------------------------------------------------------------- #

def test_reviewer_output_needs_user_accept(conn):
    with conn.transaction():
        theme = propose(conn, "tg-thesis")
        approve.accept_theme(conn, "tg-thesis", commit=False)
        thesis = make_thesis(conn, theme)
        orders = approve.accept_thesis(conn, thesis.id, commit=False)
        assert orders == []  # empty basket → nothing proposable, but ACTIVE
        status = conn.execute(
            "SELECT status FROM theses WHERE id = %s", (thesis.id,)
        ).fetchone()[0]
        assert status == "ACTIVE"
        raise psycopg.Rollback


def test_thesis_reject_fully_closes_the_thread(conn):
    with conn.transaction():
        theme = propose(conn, "tg-closed")
        approve.accept_theme(conn, "tg-closed", commit=False)
        thesis = make_thesis(conn, theme)
        approve.reject_thesis(conn, thesis.id, "chain unconvincing", commit=False)
        t_status, closed_reason = conn.execute(
            "SELECT status, closed_reason FROM themes WHERE id = %s", (theme.id,)
        ).fetchone()
        assert (t_status, closed_reason) == ("CLOSED", "thesis_rejected")
        assert conn.execute(
            "SELECT status FROM theses WHERE id = %s", (thesis.id,)
        ).fetchone()[0] == "REJECTED"
        # genealogy keeps the rejected hypothesis (§3.5)
        assert conn.execute(
            "SELECT count(*) FROM research_genealogy WHERE thesis_id = %s",
            (thesis.id,),
        ).fetchone()[0] == 1
        raise psycopg.Rollback


def test_accept_requires_reviewed_status(conn):
    with conn.transaction():
        theme = propose(conn, "tg-draft")
        thesis = make_thesis(conn, theme, status=ThesisStatus.DRAFT)
        with pytest.raises(approve.ThemeGateError, match="not REVIEWED"):
            approve.accept_thesis(conn, thesis.id, commit=False)
        raise psycopg.Rollback


# --------------------------------------------------------------------------- #
# dead — the only human death path
# --------------------------------------------------------------------------- #

def test_dead_without_positions_closes_immediately(conn):
    with conn.transaction():
        theme = propose(conn, "tg-dead")
        approve.accept_theme(conn, "tg-dead", commit=False)
        thesis = make_thesis(conn, theme, status=ThesisStatus.ACTIVE)
        repo.insert(conn, Forecast(
            thesis_id=thesis.id, statement="s", probability=0.7,
            horizon_end=date(2027, 6, 30), resolution_criterion="r",
            created_by="RESEARCH_ANALYST", model_tag="t", prompt_version="p",
            created_at=NOW,
        ))
        orders = approve.mark_dead(conn, thesis.id, "conviction gone", commit=False)
        assert orders == []
        assert conn.execute(
            "SELECT status FROM theses WHERE id = %s", (thesis.id,)
        ).fetchone()[0] == "CLOSED"
        outcome, cause = conn.execute(
            "SELECT outcome, resolution_cause FROM forecasts WHERE thesis_id = %s",
            (thesis.id,),
        ).fetchone()
        assert (outcome, cause) == ("UNRESOLVED", "MANUAL")  # invariant 7
        assert conn.execute(
            "SELECT status, closed_reason FROM themes WHERE id = %s", (theme.id,)
        ).fetchone() == ("CLOSED", "dead")
        raise psycopg.Rollback


def _open_position(conn, thesis: Thesis, symbol: str, qty: str):
    order = Order(
        experiment_id=thesis.experiment_id, thesis_id=thesis.id, symbol=symbol,
        side=OrderSide.BUY, status=OrderStatus.FILLED,
        strategy_tag=thesis.strategy_tag, proposed_qty=D(qty), approved_qty=D(qty),
        proposed_at=NOW, decided_at=NOW, created_at=NOW,
    )
    repo.insert(conn, order)
    repo.insert(conn, Trade(
        order_id=order.id, symbol=symbol, side=OrderSide.BUY, qty=D(qty),
        fill_price=D("100"), slippage_applied=D("0.05"),
        strategy_tag=thesis.strategy_tag, executed_at=NOW, created_at=NOW,
    ))
    conn.execute(
        "INSERT INTO positions (experiment_id, symbol, qty, avg_cost, as_of)"
        " VALUES (%s, %s, %s, 100, %s)",
        (thesis.experiment_id, symbol, D(qty), NOW),
    )


def test_dead_with_positions_stops_at_the_order_gate(conn):
    with conn.transaction():
        theme = propose(conn, "tg-unwind")
        approve.accept_theme(conn, "tg-unwind", commit=False)
        thesis = make_thesis(conn, theme, status=ThesisStatus.ACTIVE)
        _open_position(conn, thesis, "TGDX", "10")

        orders = approve.mark_dead(conn, thesis.id, "reversal confirmed", commit=False)
        assert [(o.symbol, o.side, o.exit_type) for o in orders] == [
            ("TGDX", OrderSide.SELL, ExitType.TIME_STOP)
        ]
        # Nothing died yet — the unwind is PROPOSED, thesis still ACTIVE
        assert conn.execute(
            "SELECT status FROM theses WHERE id = %s", (thesis.id,)
        ).fetchone()[0] == "ACTIVE"

        approve.approve_order(conn, orders[0].id, rationale="confirm", commit=False)
        assert conn.execute(
            "SELECT status FROM theses WHERE id = %s", (thesis.id,)
        ).fetchone()[0] == "CLOSED"
        assert conn.execute(
            "SELECT status, closed_reason FROM themes WHERE id = %s", (theme.id,)
        ).fetchone() == ("CLOSED", "dead")
        raise psycopg.Rollback


def test_rejecting_the_unwind_cancels_the_death(conn):
    with conn.transaction():
        theme = propose(conn, "tg-revive")
        approve.accept_theme(conn, "tg-revive", commit=False)
        thesis = make_thesis(conn, theme, status=ThesisStatus.ACTIVE)
        _open_position(conn, thesis, "TGRX", "5")

        orders = approve.mark_dead(conn, thesis.id, "maybe dead", commit=False)
        approve.reject_order(conn, orders[0].id, rationale="not dead yet", commit=False)
        assert conn.execute(
            "SELECT status FROM theses WHERE id = %s", (thesis.id,)
        ).fetchone()[0] == "ACTIVE"  # still live, still monitored
        assert conn.execute(
            "SELECT count(*) FROM orders WHERE thesis_id = %s AND status = 'PROPOSED'",
            (thesis.id,),
        ).fetchone()[0] == 0  # sibling unwinds cancelled
        raise psycopg.Rollback
