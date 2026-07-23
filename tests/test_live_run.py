"""Gate C fixture matrix for the live runner: clean append, idempotent re-run,

completeness halt, restatement guard, lot-closure updates, prefix breach."""

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
from schemas.enums import OrderSide, OrderStatus
from schemas.models import Document, Experiment, Order

START, END = date(2026, 6, 1), date(2026, 6, 26)
INGESTED = datetime(2026, 6, 27, 0, 0, tzinfo=timezone.utc)
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


def tiingo_rows(sessions, base, mutate=None):
    rows = []
    for i, s in enumerate(sessions):
        p = base + i
        row = {
            "date": f"{s}T00:00:00.000Z", "open": p, "high": p + 1, "low": p - 1,
            "close": p, "volume": 50000, "adjOpen": p, "adjHigh": p + 1,
            "adjLow": p - 1, "adjClose": p, "adjVolume": 50000,
            "splitFactor": 1, "divCash": 0,
        }
        if mutate:
            mutate(i, row)
        rows.append(row)
    return rows


def add_doc(conn, symbol, rows, tag, ingested=INGESTED):
    doc = Document(
        content_hash=f"live-{symbol}-{tag}",
        raw_bytes=json.dumps(rows).encode(),
        retrieval_url=f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}/prices",
        source_name="tiingo", ingestion_time=ingested,
        first_eligible_decision_time=ingested,
    )
    repo.insert_document(conn, doc)
    return doc


def approved_order(conn, exp_id, thesis_id, session_idx, sessions, symbol, side, qty, oid_tag):
    prev = sessions[session_idx - 1]
    o = Order(
        experiment_id=exp_id, thesis_id=thesis_id, symbol=symbol, side=side,
        status=OrderStatus.APPROVED, strategy_tag="live-test",
        proposed_qty=D(qty), approved_qty=D(qty),
        proposed_at=clock.session_close_utc(prev) - timedelta(hours=1),
        decided_at=clock.session_close_utc(prev),
        created_at=clock.session_close_utc(prev),
    )
    repo.insert(conn, o)
    return o


def setup_live(conn, order_specs):
    """Fixture AAA/BBB docs + APPROVED orders against the real EXP-001 row

    (all inside the test's rollback transaction)."""
    sessions = clock.sessions_between(START, END)
    exp = repo.fetch_one(conn, Experiment, "name = 'EXP-001'")
    thesis_id = conn.execute(
        "SELECT id FROM theses WHERE experiment_id = %s ORDER BY id DESC LIMIT 1",
        (exp.id,),
    ).fetchone()[0]
    add_doc(conn, "AAA", tiingo_rows(sessions, 100), "v1")
    add_doc(conn, "BBB", tiingo_rows(sessions, 50), "v1")
    for idx, symbol, side, qty in order_specs:
        approved_order(conn, exp.id, thesis_id, idx, sessions, symbol, side, qty, idx)
    return exp, sessions


def test_clean_append_and_idempotent_rerun(conn):
    with conn.transaction():
        exp, sessions = setup_live(conn, [(1, "AAA", OrderSide.BUY, "10")])
        mid, end = sessions[5], sessions[-1]

        s1 = live_run.run(conn, mid, commit=False)
        assert not s1.dry_run and s1.persisted_new_trades == 1
        n1 = conn.execute(
            "SELECT count(*) FROM trades t JOIN orders o ON t.order_id=o.id"
            " WHERE o.experiment_id=%s", (exp.id,)).fetchone()[0]
        assert n1 == 1

        s2 = live_run.run(conn, mid, commit=False)   # same-evening re-run
        assert s2.persisted_new_trades == 0           # duplicate run changes nothing

        s3 = live_run.run(conn, end, commit=False)    # later session extends
        assert s3.persisted_new_trades == 0           # no new orders → no new trades
        assert s3.replayed_sessions == len(sessions)
        # Scope to the fixture window: the real EXP-001 has committed live NAV
        # rows from genesis onward, outside this test's June replay.
        navs = conn.execute(
            "SELECT count(*) FROM daily_nav WHERE experiment_id=%s"
            " AND nav_date BETWEEN %s AND %s", (exp.id, START, END)
        ).fetchone()[0]
        assert navs == len(sessions)
        raise psycopg.Rollback


def test_completeness_gate_halts_before_replay(conn):
    with conn.transaction():
        exp, sessions = setup_live(conn, [])
        # First session past the newest ingested bar — guaranteed missing,
        # regardless of how far the real nightly ingestion has advanced. Read
        # the DOCUMENTS the gate itself reads (price_bars only advances when
        # a live run persists, so it can lag a fresh ingest by a session).
        from data.adapters.tiingo import parse_bars
        from data.price_bars import _latest_docs_by_symbol
        newest_bar = max(
            (b.session
             for sym, doc in _latest_docs_by_symbol(conn).items()
             for b in parse_bars(doc, sym)),
            default=END,
        )
        future = clock.next_session(max(newest_bar, END))
        with pytest.raises(live_run.CompletenessGateFailure):
            live_run.run(conn, future, commit=False)
        raise psycopg.Rollback


def test_restatement_guard_pins_and_classifies(conn):
    with conn.transaction():
        exp, sessions = setup_live(conn, [(1, "AAA", OrderSide.BUY, "10")])
        live_run.run(conn, sessions[5], commit=False)

        # A later document RESTATES a raw open in the persisted range …
        def corrupt(i, row):
            if i == 2:
                row["open"] = row["open"] + 5  # bad print correction
        add_doc(conn, "AAA", tiingo_rows(sessions, 100, corrupt), "v2",
                ingested=INGESTED + timedelta(days=1))

        summary = live_run.run(conn, sessions[6], commit=False)
        assert summary.pinned_symbols == ("AAA",)     # old doc stayed pinned
        n_rev = conn.execute(
            "SELECT count(*) FROM data_incidents WHERE incident_class='REVISION'"
            " AND symbol='AAA'").fetchone()[0]
        assert n_rev >= 1
        # Ledger still replays from the pinned bars — no breach, same trades
        n_trades = conn.execute(
            "SELECT count(*) FROM trades t JOIN orders o ON t.order_id=o.id"
            " WHERE o.experiment_id=%s", (exp.id,)).fetchone()[0]
        assert n_trades == 1
        raise psycopg.Rollback


def test_adjusted_rescale_does_not_trigger_guard(conn):
    with conn.transaction():
        exp, sessions = setup_live(conn, [(1, "AAA", OrderSide.BUY, "10")])
        live_run.run(conn, sessions[5], commit=False)

        # New doc: raw identical, adjusted series rescaled (a dividend landed)
        def rescale(i, row):
            row["adjOpen"] = row["adjOpen"] * 0.98
            row["adjClose"] = row["adjClose"] * 0.98
        add_doc(conn, "AAA", tiingo_rows(sessions, 100, rescale), "v2",
                ingested=INGESTED + timedelta(days=1))
        summary = live_run.run(conn, sessions[6], commit=False)
        assert summary.pinned_symbols == ()           # adopted without incident
        raise psycopg.Rollback


def test_lot_closure_updates_previous_rows(conn):
    with conn.transaction():
        exp, sessions = setup_live(conn, [
            (1, "AAA", OrderSide.BUY, "10"),
            (8, "AAA", OrderSide.SELL, "10"),
        ])
        live_run.run(conn, sessions[5], commit=False)     # only the buy so far
        open_lot = conn.execute(
            "SELECT l.id, l.closing_trade_id FROM tax_lots l"
            " JOIN trades t ON l.opening_trade_id=t.id"
            " JOIN orders o ON t.order_id=o.id WHERE o.experiment_id=%s", (exp.id,)
        ).fetchone()
        assert open_lot[1] is None

        s2 = live_run.run(conn, sessions[10], commit=False)  # sell fills at idx 8
        assert s2.persisted_new_trades == 1 and s2.updated_lots == 1
        closed = conn.execute(
            "SELECT closing_trade_id, realized_pnl FROM tax_lots WHERE id=%s",
            (open_lot[0],)).fetchone()
        assert closed[0] is not None and closed[1] is not None  # SAME row, updated
        raise psycopg.Rollback


def test_prefix_breach_halts_without_persisting(conn):
    with conn.transaction():
        exp, sessions = setup_live(conn, [(1, "AAA", OrderSide.BUY, "10")])
        live_run.run(conn, sessions[5], commit=False)

        # Simulate history diverging past the guard: tamper the persisted copy
        # via direct SQL (trades are immutable — the trigger blocks UPDATE, so
        # tamper the order linkage instead: change qty on the ORDER and replay
        # can't reproduce the persisted trade). Simplest honest tamper: bump
        # the approved order's qty so the replay fills a different quantity.
        conn.execute(
            "UPDATE orders SET approved_qty = approved_qty + 5"
            " WHERE experiment_id = %s AND status = 'APPROVED'", (exp.id,))
        with pytest.raises(live_run.PrefixBreach):
            live_run.run(conn, sessions[6], commit=False)
        n_trades = conn.execute(
            "SELECT count(*) FROM trades t JOIN orders o ON t.order_id=o.id"
            " WHERE o.experiment_id=%s", (exp.id,)).fetchone()[0]
        assert n_trades == 1  # nothing new persisted after the breach
        raise psycopg.Rollback


def test_dry_run_before_genesis(conn, monkeypatch):
    monkeypatch.setattr(live_run, "GENESIS", date(2026, 7, 20))
    with conn.transaction():
        summary = live_run.run(conn, date(2026, 7, 16), commit=False)
        assert summary.dry_run is True
        raise psycopg.Rollback
