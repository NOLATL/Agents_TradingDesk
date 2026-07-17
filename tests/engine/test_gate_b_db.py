"""Gate B end-to-end through every real seam: synthetic Tiingo documents →

price_bars rebuild → manifest → load_input → simulate → persist → reconstruct
→ byte-compare, plus idempotent re-persist. Includes a quarantine timeout so
the FORCED_LIQUIDATION order synthesis and NAV taint land in real rows.
"""

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

from analytics.engine import clock, persist
from analytics.engine.loop import simulate
from data import repository as repo
from data.db import dsn
from data.migrate import migrate
from data.price_bars import rebuild_price_bars
from schemas.enums import OrderSide, OrderStatus, ThesisStatus
from schemas.models import Document, Experiment, Order, Thesis

START, END = date(2026, 6, 1), date(2026, 6, 26)
INGESTED = datetime(2026, 6, 27, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def tiingo_rows(sessions, base: int, stop_after: int | None = None):
    rows = []
    for i, s in enumerate(sessions):
        if stop_after is not None and i > stop_after:
            break
        p = base + i
        rows.append({
            "date": f"{s}T00:00:00.000Z", "open": p, "high": p + 1, "low": p - 1,
            "close": p, "volume": 50000, "adjOpen": p, "adjHigh": p + 1,
            "adjLow": p - 1, "adjClose": p, "adjVolume": 50000,
            "splitFactor": 1, "divCash": 0,
        })
    return rows


def setup_fixtures(conn):
    sessions = clock.sessions_between(START, END)
    exp = Experiment(name="GATEB-E2E", registry={}, created_at=INGESTED)
    repo.insert(conn, exp)
    thesis = Thesis(
        experiment_id=exp.id, title="gate-b fixture", strategy_tag="gate-b",
        status=ThesisStatus.ACTIVE, causal_chain="fixture",
        exposure_basket_approach="fixture", valuation_check="fixture",
        exit_criteria={"PRICE_STOP": "x", "CAUSAL_BREAK": "x",
                       "VALUATION_EXIT": "x", "TIME_STOP": "x"},
        model_tag="fixture", prompt_version="fixture", created_at=INGESTED,
    )
    repo.insert(conn, thesis)

    for symbol, base, stop in (("AAA", 100, None), ("BBB", 50, 9),
                               ("SPY", 400, None), ("RSP", 150, None)):
        doc = Document(
            content_hash=f"gate-b-{symbol}",
            raw_bytes=json.dumps(tiingo_rows(sessions, base, stop)).encode(),
            retrieval_url=f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}/prices",
            source_name="tiingo", ingestion_time=INGESTED,
            first_eligible_decision_time=INGESTED,
        )
        repo.insert_document(conn, doc)

    def approved(order_id_hint, session_idx, symbol, side, qty):
        prev = sessions[session_idx - 1]
        o = Order(
            experiment_id=exp.id, thesis_id=thesis.id, symbol=symbol, side=side,
            status=OrderStatus.APPROVED, strategy_tag="gate-b",
            proposed_qty=qty, approved_qty=qty,
            proposed_at=clock.session_close_utc(prev) - timedelta(hours=1),
            decided_at=clock.session_close_utc(prev),
            created_at=clock.session_close_utc(prev),
        )
        repo.insert(conn, o)
        return o

    from decimal import Decimal
    approved(1, 1, "AAA", OrderSide.BUY, Decimal("10"))
    approved(2, 2, "BBB", OrderSide.BUY, Decimal("20"))   # gets quarantined later
    approved(3, 6, "AAA", OrderSide.SELL, Decimal("10"))  # full close
    return exp


def comparable(result):
    """The Gate B comparison surface: everything reconstruct_result rebuilds."""
    d = asdict(result)
    d["benchmark_navs"] = sorted(
        d["benchmark_navs"], key=lambda b: (str(b["session"]), b["benchmark_name"])
    )
    keep = {"experiment_id", "trades", "lots", "navs", "benchmark_navs"}
    return persist.canonical_json({k: v for k, v in d.items() if k in keep})


def test_gate_b_end_to_end(conn):
    with conn.transaction():
        exp = setup_fixtures(conn)
        rebuild_price_bars(conn)

        cfg = persist.engine_config_from_yaml()
        manifest = persist.build_manifest(conn, exp.id, START, END, cfg)
        assert len(manifest.approved_order_ids) == 3
        assert manifest.document_hashes  # bars trace to immutable documents

        inp1 = persist.load_input(conn, manifest, cfg)
        inp2 = persist.load_input(conn, manifest, cfg)
        r1, r2 = simulate(inp1), simulate(inp2)

        # THE headline: bit-identical replay through the full DB path.
        assert persist.canonical_bytes(r1) == persist.canonical_bytes(r2)

        # Quarantine timeout really happened: forced trade + taint in the result
        forced = [t for t in r1.trades if t.forced]
        assert len(forced) == 1 and forced[0].symbol == "BBB"
        assert r1.navs[-1].contains_forced_resolution is True

        run_id = persist.persist_run(conn, r1, manifest)

        # Idempotent re-persist: same manifest+result → same run row, no dupes.
        assert persist.persist_run(conn, r1, manifest) == run_id
        n_trades = conn.execute("SELECT count(*) FROM trades").fetchone()[0]
        assert n_trades == len(r1.trades)

        # FORCED_LIQUIDATION order synthesized with no human fields
        forced_orders = conn.execute(
            "SELECT human_rationale, approved_qty FROM orders"
            " WHERE status = 'FORCED_LIQUIDATION'"
        ).fetchall()
        assert forced_orders == [(None, None)]

        # Taint flag landed on the real NAV rows from the forced session onward
        tainted = conn.execute(
            "SELECT count(*) FROM daily_nav WHERE experiment_id = %s"
            " AND contains_forced_resolution", (exp.id,),
        ).fetchone()[0]
        assert tainted > 0

        # Persistence is lossless: reconstruct and byte-compare the surface.
        rebuilt = persist.reconstruct_result(conn, run_id)
        assert comparable(rebuilt) == comparable(r1)
        raise psycopg.Rollback


def test_determinism_breach_detected(conn):
    """Same manifest with a different result hash must refuse to persist."""
    from dataclasses import replace as dc_replace

    with conn.transaction():
        exp = setup_fixtures(conn)
        rebuild_price_bars(conn)
        cfg = persist.engine_config_from_yaml()
        manifest = persist.build_manifest(conn, exp.id, START, END, cfg)
        result = simulate(persist.load_input(conn, manifest, cfg))
        persist.persist_run(conn, result, manifest)

        tampered = dc_replace(result, navs=result.navs[:-1])  # a "different" outcome
        with pytest.raises(persist.DeterminismBreach):
            persist.persist_run(conn, tampered, manifest)
        raise psycopg.Rollback
