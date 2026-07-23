"""The engine's only DB-facing code: manifest building, input loading,

canonical serialization, and persistence. The engine core (loop.py etc.)
never touches a connection — this module is the shell around the pure core.

Idempotency / determinism contract (simulation_runs):
- same manifest_hash + same result_hash  -> re-persist is a no-op
- same manifest_hash + DIFFERENT result  -> DeterminismBreach (operational
  breaker semantics: a duplicate run that disagrees is a halt condition)
"""

import hashlib
import json
import subprocess
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal

from analytics.engine import clock
from analytics.engine.state import (
    ApprovedOrder,
    Bar,
    EngineConfig,
    ReplayManifest,
    ResolutionEvent,
    SimulationInput,
    SimulationResult,
    SlippageTier,
)
from config.loader import CONFIG
from data import repository as repo
from schemas.enums import (
    ExitType,
    IncidentStatus,
    OrderSide,
    OrderStatus,
    ProvenanceType,
)
from schemas.models import (
    Assertion,
    BenchmarkNav,
    DailyNav,
    DataIncident,
    Order,
    Position,
    PriceBar,
    SimulationRun,
    TaxLot,
    Trade,
)


class DeterminismBreach(Exception):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def engine_config_from_yaml() -> EngineConfig:
    """Base config from YAML only — no universe gating, and basket falls back
    to the whole momentum universe (the pre-unfreeze behavior; note it never
    matched the 5-name active-theme basket the config comments described).
    Production uses data.universe.engine_config_from_db, which overrides
    momentum_universe/basket point-in-time from the themes tables; the YAML
    momentum_universe key is genesis documentation from the 2026-07-22
    cutover onward.
    """
    e = CONFIG["engine"]
    return EngineConfig(
        initial_cash=Decimal(e["initial_cash"]),
        slippage_tiers=tuple(
            SlippageTier(Decimal(t["vol_threshold"]), Decimal(t["bps"]))
            for t in sorted(e["slippage"]["tiers"], key=lambda t: Decimal(t["vol_threshold"]),
                            reverse=True)
        ),
        default_slippage_bps=Decimal(e["slippage"]["default_bps"]),
        quarantine_window_sessions=int(e["quarantine_window_sessions"]),
        max_single_name_pct=Decimal(e["caps"]["max_single_name_pct"]),
        max_theme_pct=Decimal(e["caps"]["max_theme_pct"]),
        max_sector_pct=Decimal(e["caps"]["max_sector_pct"]),
        drawdown_breaker_pct=Decimal(e["breakers"]["drawdown_pct"]),
        spy_symbol=e["controls"]["spy_symbol"],
        ewp_symbol=e["controls"]["ewp_symbol"],
        momentum_universe=tuple(e["controls"]["momentum_universe"]),
        momentum_top_n=int(e["controls"]["momentum_top_n"]),
        basket=tuple(e["controls"]["momentum_universe"]),
        benchmark_rebalance_sessions=int(e["controls"]["benchmark_rebalance_sessions"]),
    )


def config_hash(cfg: EngineConfig) -> str:
    return _sha(canonical_json(asdict(cfg)).encode())


# --------------------------------------------------------------------------- #
# Canonical serialization (the Gate B byte-comparison target)
# --------------------------------------------------------------------------- #

def _canon(value):
    if isinstance(value, Decimal):
        return f"D:{value}"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canon(v) for v in value]
    return value


def canonical_json(obj) -> str:
    return json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"), default=str)


def canonical_bytes(result: SimulationResult) -> bytes:
    return canonical_json(asdict(result)).encode()


def result_hash(result: SimulationResult) -> str:
    return _sha(canonical_bytes(result))


# --------------------------------------------------------------------------- #
# Manifest + input loading (the replayable input set)
# --------------------------------------------------------------------------- #

def build_manifest(
    conn, experiment_id: int, session_start: date, session_end: date,
    cfg: EngineConfig,
) -> ReplayManifest:
    orders = repo.fetch(
        conn, Order,
        "experiment_id = %s AND status = 'APPROVED' AND decided_at IS NOT NULL"
        " ORDER BY decided_at, id",
        (experiment_id,),
    )
    resolutions = repo.fetch(
        conn, Assertion,
        "type = 'HUMAN' AND value->>'kind' = 'quarantine_resolution' ORDER BY id",
    )
    bar_pins = repo.fetch(
        conn, Assertion,
        "type = 'HUMAN' AND value->>'kind' = 'bar_pin' ORDER BY id",
    )
    doc_hashes = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT source_doc_hash FROM price_bars ORDER BY 1"
        )
    ]
    return ReplayManifest(
        experiment_id=experiment_id,
        code_commit=code_commit(),
        config_hash=config_hash(cfg),
        document_hashes=tuple(doc_hashes),
        approved_order_ids=tuple(o.id for o in orders),
        resolution_assertion_ids=tuple(a.id for a in resolutions),
        session_start=session_start,
        session_end=session_end,
        initial_cash=str(cfg.initial_cash),
        bar_pin_assertion_ids=tuple(a.id for a in bar_pins),
    )


def manifest_hash(manifest: ReplayManifest) -> str:
    return _sha(canonical_json(asdict(manifest)).encode())


def load_bar_matrix(conn) -> dict[str, dict[date, Bar]]:
    rows = repo.fetch(conn, PriceBar, "TRUE ORDER BY symbol, session_date")
    out: dict[str, dict[date, Bar]] = {}
    for r in rows:
        out.setdefault(r.symbol, {})[r.session_date] = Bar(
            session=r.session_date,
            open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume,
            adj_open=r.adj_open, adj_high=r.adj_high, adj_low=r.adj_low,
            adj_close=r.adj_close, adj_volume=r.adj_volume,
            split_factor=r.split_factor, div_cash=r.div_cash,
        )
    return out


def load_input(conn, manifest: ReplayManifest, cfg: EngineConfig) -> SimulationInput:
    if config_hash(cfg) != manifest.config_hash:
        raise DeterminismBreach("config hash differs from manifest — not the same experiment")
    orders = []
    for oid in manifest.approved_order_ids:
        o = repo.fetch_one(conn, Order, "id = %s", (oid,))
        orders.append(
            ApprovedOrder(
                order_id=o.id, symbol=o.symbol, side=o.side,
                qty=o.approved_qty if o.approved_qty is not None else o.proposed_qty,
                decided_at=o.decided_at, strategy_tag=o.strategy_tag,
                exit_type=o.exit_type,
            )
        )
    resolutions = []
    for aid in manifest.resolution_assertion_ids:
        a = repo.fetch_one(conn, Assertion, "id = %s", (aid,))
        v = a.value
        resolutions.append(
            ResolutionEvent(
                assertion_id=a.id,
                symbol=v["symbol"],
                effective_session=date.fromisoformat(v["effective_session"]),
                cash_delta=Decimal(str(v.get("cash_delta", "0"))),
                close_position=bool(v.get("close_position", False)),
            )
        )
    bars = load_bar_matrix(conn)
    bars = _apply_bar_pins(conn, bars, manifest.bar_pin_assertion_ids)
    return SimulationInput(
        experiment_id=manifest.experiment_id,
        sessions=clock.sessions_between(manifest.session_start, manifest.session_end),
        bars=bars,
        approved_orders=tuple(sorted(orders, key=lambda o: (o.decided_at, o.order_id))),
        resolutions=tuple(resolutions),
        config=cfg,
    )


def _apply_bar_pins(
    conn, bars: dict[str, dict[date, Bar]], pin_assertion_ids: tuple[int, ...]
) -> dict[str, dict[date, Bar]]:
    """Overlay pinned documents' bars for sessions <= through_session (Stage 3

    restatement resolution). The engine still sees one BarMatrix; the ledger's
    history replays from the bars it actually filled on."""
    if not pin_assertion_ids:
        return bars
    from data.adapters.tiingo import parse_bars as tiingo_parse
    from schemas.models import Document

    for aid in pin_assertion_ids:
        assertion = repo.fetch_one(conn, Assertion, "id = %s", (aid,))
        v = assertion.value
        symbol = v["symbol"]
        through = date.fromisoformat(v["through_session"])
        pinned_doc = repo.fetch_one(
            conn, Document, "content_hash = %s", (v["doc_hash_old"],)
        )
        if pinned_doc is None:
            raise DeterminismBreach(f"bar_pin A{aid}: pinned document not found")
        overlay = {
            b.session: Bar(
                session=b.session, open=b.open, high=b.high, low=b.low,
                close=b.close, volume=b.volume, adj_open=b.adj_open,
                adj_high=b.adj_high, adj_low=b.adj_low, adj_close=b.adj_close,
                adj_volume=b.adj_volume, split_factor=b.split_factor,
                div_cash=b.div_cash,
            )
            for b in tiingo_parse(pinned_doc, symbol) if b.session <= through
        }
        merged = dict(bars.get(symbol, {}))
        merged.update(overlay)
        bars = {**bars, symbol: merged}
    return bars


# --------------------------------------------------------------------------- #
# Persistence (thin writer of a finished result)
# --------------------------------------------------------------------------- #

def persist_run(conn, result: SimulationResult, manifest: ReplayManifest) -> int:
    """Write the result in one transaction. Returns the simulation_runs id."""
    m_hash = manifest_hash(manifest)
    r_hash = result_hash(result)

    existing = repo.fetch_one(conn, SimulationRun, "manifest_hash = %s", (m_hash,))
    if existing is not None:
        if existing.result_hash != r_hash:
            raise DeterminismBreach(
                f"manifest {m_hash[:12]} previously produced {existing.result_hash[:12]}, "
                f"now {r_hash[:12]} — ledger determinism violated"
            )
        return existing.id  # idempotent no-op

    with conn.transaction():
        # Forced liquidations get their synthesized system orders (§6 exception).
        forced_order_ids: dict[int, int] = {}
        for t in result.trades:
            if t.forced:
                order = Order(
                    experiment_id=result.experiment_id,
                    thesis_id=_any_thesis_id(conn, result.experiment_id),
                    symbol=t.symbol, side=t.side,
                    status=OrderStatus.FORCED_LIQUIDATION,
                    strategy_tag=t.strategy_tag,
                    proposed_qty=t.qty, approved_qty=None,
                    human_rationale=None,
                    proposed_at=clock.session_open_utc(t.session),
                    created_at=clock.session_open_utc(t.session),
                )
                repo.insert(conn, order)
                forced_order_ids[t.trade_seq] = order.id

        trade_ids: dict[int, int] = {}
        for t in result.trades:
            row = Trade(
                order_id=forced_order_ids.get(t.trade_seq, t.order_id),
                symbol=t.symbol, side=t.side, qty=t.qty,
                fill_price=t.fill_price, slippage_applied=t.slippage_applied,
                strategy_tag=t.strategy_tag, exit_type=t.exit_type,
                executed_at=clock.session_open_utc(t.session),
                created_at=clock.session_open_utc(t.session),
            )
            repo.insert(conn, row)
            trade_ids[t.trade_seq] = row.id

        for lot in result.lots:
            repo.insert(conn, TaxLot(
                opening_trade_id=trade_ids[lot.opening_trade_seq],
                closing_trade_id=(trade_ids[lot.closing_trade_seq]
                                  if lot.closing_trade_seq else None),
                symbol=lot.symbol, qty=lot.qty, cost_basis=lot.cost_basis,
                acquisition_date=lot.acquisition_date,
                closed_at=(clock.session_open_utc(lot.closed_session)
                           if lot.closed_session else None),
                realized_pnl=lot.realized_pnl,
                holding_period_days=lot.holding_period_days,
                wash_sale_candidate=lot.wash_sale_candidate,
            ))

        for nav in result.navs:
            conn.execute(
                "INSERT INTO daily_nav (experiment_id, nav_date, nav, cash,"
                " positions_value, contains_forced_resolution)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (experiment_id, nav_date) DO UPDATE SET nav = EXCLUDED.nav,"
                " cash = EXCLUDED.cash, positions_value = EXCLUDED.positions_value,"
                " contains_forced_resolution = EXCLUDED.contains_forced_resolution",
                (result.experiment_id, nav.session, nav.nav, nav.cash,
                 nav.positions_value, nav.contains_forced_resolution),
            )
        for b in result.benchmark_navs:
            conn.execute(
                "INSERT INTO benchmarks_nav (experiment_id, benchmark_name, nav_date,"
                " nav, contains_forced_resolution) VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (experiment_id, benchmark_name, nav_date) DO UPDATE SET"
                " nav = EXCLUDED.nav,"
                " contains_forced_resolution = EXCLUDED.contains_forced_resolution",
                (result.experiment_id, b.benchmark_name, b.session, b.nav,
                 b.contains_forced_resolution),
            )
        for snap in result.position_snapshots:
            repo.insert(conn, Position(
                experiment_id=result.experiment_id, symbol=snap.symbol,
                qty=snap.qty, avg_cost=snap.avg_cost, market_value=snap.market_value,
                frozen=snap.frozen, frozen_mark=snap.frozen_mark,
                as_of=clock.session_close_utc(snap.session),
            ))
        for inc in result.incidents:
            repo.insert(conn, DataIncident(
                experiment_id=result.experiment_id,
                incident_class=inc.incident_class,
                status={
                    "OPEN": IncidentStatus.OPEN,
                    "RESOLVED": IncidentStatus.RESOLVED_HUMAN,
                    "FORCED": IncidentStatus.FORCED_LIQUIDATION,
                }[inc.phase.value],
                source_name="engine",
                symbol=inc.symbol,
                description=inc.description,
                detected_at=clock.session_open_utc(inc.detected_session),
                resolved_at=(clock.session_close_utc(inc.resolved_session)
                             if inc.resolved_session else None),
                resolution_assertion_id=inc.resolution_assertion_id,
            ))

        run = SimulationRun(
            experiment_id=result.experiment_id,
            manifest={**asdict(manifest),
                      "session_start": manifest.session_start.isoformat(),
                      "session_end": manifest.session_end.isoformat()},
            manifest_hash=m_hash,
            result_hash=r_hash,
            code_commit=manifest.code_commit,
            created_at=clock.session_close_utc(result.navs[-1].session)
            if result.navs else datetime.now(timezone.utc),
        )
        repo.insert(conn, run)
    return run.id


def _any_thesis_id(conn, experiment_id: int) -> int:
    """Forced-liquidation orders need a thesis FK; use the experiment's most

    recent thesis (the position being liquidated belongs to it in practice)."""
    row = conn.execute(
        "SELECT id FROM theses WHERE experiment_id = %s ORDER BY id DESC LIMIT 1",
        (experiment_id,),
    ).fetchone()
    if row is None:
        raise ValueError("no thesis exists for forced-liquidation order FK")
    return row[0]


def reconstruct_result(conn, run_id: int) -> "SimulationResult | None":
    """Reload a persisted run's trades/lots/navs and rebuild seq-keyed rows —

    used by the round-trip half of the replay test. Trades were inserted in
    seq order, so ascending id order recovers the sequence."""
    from analytics.engine.state import LotRow, NavRow, TradeRow
    from analytics.engine.state import BenchmarkNavRow

    run = repo.fetch_one(conn, SimulationRun, "id = %s", (run_id,))
    if run is None:
        return None
    exp = run.experiment_id
    trades = repo.fetch(conn, Trade, "TRUE ORDER BY id")
    id_to_seq = {}
    trade_rows = []
    forced_ids = {
        r[0] for r in conn.execute(
            "SELECT t.id FROM trades t JOIN orders o ON t.order_id = o.id"
            " WHERE o.status = 'FORCED_LIQUIDATION'"
        )
    }
    for seq, t in enumerate(trades, 1):
        id_to_seq[t.id] = seq
        trade_rows.append(TradeRow(
            trade_seq=seq,
            order_id=0 if t.id in forced_ids else t.order_id,
            symbol=t.symbol, side=t.side, qty=t.qty,
            fill_price=t.fill_price, slippage_applied=t.slippage_applied,
            strategy_tag=t.strategy_tag, session=t.executed_at.date(),
            exit_type=t.exit_type, forced=t.id in forced_ids,
        ))
    lots = repo.fetch(conn, TaxLot, "TRUE ORDER BY id")
    lot_rows = tuple(
        LotRow(
            lot_seq=seq,
            opening_trade_seq=id_to_seq[lot.opening_trade_id],
            symbol=lot.symbol, qty=lot.qty, cost_basis=lot.cost_basis,
            acquisition_date=lot.acquisition_date,
            closing_trade_seq=(id_to_seq[lot.closing_trade_id]
                               if lot.closing_trade_id else None),
            closed_session=lot.closed_at.date() if lot.closed_at else None,
            realized_pnl=lot.realized_pnl,
            holding_period_days=lot.holding_period_days,
            wash_sale_candidate=lot.wash_sale_candidate,
        )
        for seq, lot in enumerate(lots, 1)
    )
    navs = tuple(
        NavRow(n.nav_date, n.nav, n.cash, n.positions_value, n.contains_forced_resolution)
        for n in repo.fetch(conn, DailyNav, "experiment_id = %s ORDER BY nav_date", (exp,))
    )
    bench = tuple(
        BenchmarkNavRow(b.benchmark_name, b.nav_date, b.nav, b.contains_forced_resolution)
        for b in repo.fetch(
            conn, BenchmarkNav,
            "experiment_id = %s ORDER BY nav_date, benchmark_name", (exp,))
    )
    return SimulationResult(
        experiment_id=exp,
        trades=tuple(trade_rows),
        lots=lot_rows,
        navs=navs,
        benchmark_navs=bench,
        position_snapshots=(),  # not reconstructed; covered by nav identity
        incidents=(),
        breaker_events=(),
    )
