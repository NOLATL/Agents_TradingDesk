"""The Stage 3 live runner: genesis replay + prefix verification + incremental

persist (DECISIONS.md 2026-07-17 Stage 3). This is the ONLY code path that
writes the live experiment's ledger.

Sequence (all under one Postgres advisory lock — rebuild does DELETE):
  1. restatement guard   — a newly fetched Tiingo doc may only replace a
                           symbol's bars if raw OHLCV/split/div are unchanged
                           for all sessions <= the last persisted live session;
                           otherwise the old doc stays pinned + REVISION
                           incident + alert (adj_* fields are free to rescale —
                           dividends legitimately rescale the adjusted series)
  2. rebuild price_bars  — with any guard pins applied
  3. completeness gate   — every active-universe symbol has a bar for the
                           target session, else halt BEFORE replay (late data
                           never generates orders — Gate C)
  4. genesis replay      — simulate() from genesis to the target session
  5. prefix verification — persisted live trades must be a prefix of the
                           replay's trades; breach → halt + incident + alert
                           (resolution: HUMAN bar_pin assertion)
  6. incremental persist — append suffix trades (+ synthesized forced orders),
                           positionally update lots (replay determinism makes
                           the prefix stable), upsert NAV/benchmarks/snapshots/
                           incidents, record today's simulation_runs row

Fills are historical facts; never restated.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from analytics.engine import clock, persist
from analytics.engine.loop import simulate
from analytics.engine.state import ReplayManifest, SimulationResult, TradeRow
from config.loader import CONFIG
from data import repository as repo
from data.adapters.tiingo import parse_bars
from data.price_bars import _latest_docs_by_symbol, rebuild_price_bars
from data.reconcile import raise_incident
from ops.alerts import notify
from schemas.enums import IncidentClass, IncidentStatus, OrderStatus
from schemas.models import Document, Experiment, Order, PriceBar, Trade, TaxLot

ADVISORY_LOCK_KEY = 0x7261DE5C  # arbitrary fixed key for ingest→persist mutual exclusion

STAGE3 = CONFIG["stage3"]
GENESIS = date.fromisoformat(STAGE3["genesis_date"])


class PrefixBreach(Exception):
    pass


class CompletenessGateFailure(Exception):
    pass


@dataclass(frozen=True)
class LiveRunSummary:
    session: date
    replayed_sessions: int
    persisted_new_trades: int
    updated_lots: int
    nav: Decimal
    tainted: bool
    pinned_symbols: tuple[str, ...]
    dry_run: bool


def live_experiment(conn) -> Experiment:
    exp = repo.fetch_one(conn, Experiment, "name = %s", (STAGE3["live_experiment"],))
    if exp is None:
        raise RuntimeError(f"live experiment {STAGE3['live_experiment']} missing")
    return exp


def active_universe(cfg) -> list[str]:
    return sorted(set(cfg.momentum_universe) | {cfg.spy_symbol, cfg.ewp_symbol})


# --------------------------------------------------------------------------- #
# 1. Restatement guard
# --------------------------------------------------------------------------- #

RAW_FIELDS = ("open", "high", "low", "close", "volume", "split_factor", "div_cash")


def _persisted_bars(conn, symbol: str, through: date) -> dict[date, PriceBar]:
    rows = repo.fetch(
        conn, PriceBar, "symbol = %s AND session_date <= %s", (symbol, through)
    )
    return {r.session_date: r for r in rows}


def restatement_guard(
    conn, experiment_id: int, last_live_session: date | None
) -> dict[str, Document]:
    """Returns pins: symbol -> previously-adopted Document to keep, for any

    symbol whose newest document restates raw history. Also raises the
    classified REVISION incidents."""
    if last_live_session is None:
        return {}  # nothing persisted yet — nothing can be restated
    pins: dict[str, Document] = {}
    latest = _latest_docs_by_symbol(conn)
    for symbol in sorted(latest):
        current_rows = _persisted_bars(conn, symbol, last_live_session)
        if not current_rows:
            continue
        current_hash = next(iter(current_rows.values())).source_doc_hash
        candidate = latest[symbol]
        if candidate.content_hash == current_hash:
            continue
        new_bars = {b.session: b for b in parse_bars(candidate, symbol)}
        diffs = []
        for session, row in sorted(current_rows.items()):
            nb = new_bars.get(session)
            if nb is None:
                diffs.append((session, "bar", "present", "MISSING"))
                continue
            for field in RAW_FIELDS:
                old, new = getattr(row, field), getattr(nb, field)
                if Decimal(str(old)) != Decimal(str(new)):
                    diffs.append((session, field, old, new))
        if diffs:
            old_doc = repo.fetch_one(conn, Document, "content_hash = %s", (current_hash,))
            pins[symbol] = old_doc
            detail = "; ".join(f"{s} {f}: {o}→{n}" for s, f, o, n in diffs[:5])
            raise_incident(
                conn,
                incident_class=IncidentClass.REVISION,
                source_name="tiingo",
                symbol=symbol,
                description=(
                    f"raw history restated in doc {candidate.content_hash[:12]} "
                    f"vs adopted {current_hash[:12]} ({len(diffs)} diffs: {detail}). "
                    f"Old doc pinned; resolve with a HUMAN bar_pin assertion."
                ),
            )
            notify(
                f"RESTATEMENT: {symbol}",
                f"Tiingo restated raw history for {symbol} ({len(diffs)} field diffs). "
                f"Old document pinned; bars will go stale until resolved.",
                priority="high", tags="warning",
            )
    return pins


# --------------------------------------------------------------------------- #
# 3. Completeness gate
# --------------------------------------------------------------------------- #

def completeness_gate(conn, symbols: list[str], session: date) -> None:
    missing = [
        s for s in symbols
        if not conn.execute(
            "SELECT 1 FROM price_bars WHERE symbol = %s AND session_date = %s",
            (s, session),
        ).fetchone()
    ]
    if missing:
        raise CompletenessGateFailure(
            f"no bar for {missing} on session {session} — halting before replay; "
            f"late/failed data never generates orders"
        )


# --------------------------------------------------------------------------- #
# 5. Prefix verification
# --------------------------------------------------------------------------- #

def _comparison_key(t: TradeRow) -> tuple:
    return (t.symbol, t.side.value, str(t.qty), str(t.fill_price),
            str(t.slippage_applied), t.strategy_tag, str(t.session),
            t.exit_type.value if t.exit_type else None, t.forced)


def persisted_live_trades(conn, experiment_id: int) -> list[tuple[int, Trade, bool]]:
    """(trade_db_id, Trade, forced) for the live experiment, insertion order."""
    rows = conn.execute(
        "SELECT t.id, o.status = 'FORCED_LIQUIDATION' FROM trades t"
        " JOIN orders o ON t.order_id = o.id WHERE o.experiment_id = %s ORDER BY t.id",
        (experiment_id,),
    ).fetchall()
    out = []
    for trade_id, forced in rows:
        t = repo.fetch_one(conn, Trade, "id = %s", (trade_id,))
        out.append((trade_id, t, forced))
    return out


def verify_prefix(
    persisted: list[tuple[int, Trade, bool]], result: SimulationResult
) -> dict[int, int]:
    """Assert persisted trades are a prefix of the replay. Returns

    trade_seq -> db id for the prefix."""
    if len(persisted) > len(result.trades):
        raise PrefixBreach(
            f"{len(persisted)} persisted trades but replay produced only "
            f"{len(result.trades)} — history shrank"
        )
    seq_to_id: dict[int, int] = {}
    for (db_id, db_trade, forced), replay_trade in zip(persisted, result.trades):
        db_key = (db_trade.symbol, db_trade.side.value, str(db_trade.qty),
                  str(db_trade.fill_price), str(db_trade.slippage_applied),
                  db_trade.strategy_tag, str(db_trade.executed_at.date()),
                  db_trade.exit_type.value if db_trade.exit_type else None, forced)
        if db_key != _comparison_key(replay_trade):
            raise PrefixBreach(
                f"trade #{db_id} diverged: persisted {db_key} vs replay "
                f"{_comparison_key(replay_trade)} — vendor restatement got past "
                f"the guard; resolve with a bar_pin assertion"
            )
        seq_to_id[replay_trade.trade_seq] = db_id
    return seq_to_id


# --------------------------------------------------------------------------- #
# 6. Incremental persist
# --------------------------------------------------------------------------- #

def persist_incremental(
    conn, result: SimulationResult, manifest: ReplayManifest,
    seq_to_id: dict[int, int], n_persisted: int,
) -> tuple[int, int]:
    """Append suffix trades/lots, positionally update existing lots, upsert

    everything else. Returns (new_trades, updated_lots)."""
    exp = result.experiment_id
    thesis_row = conn.execute(
        "SELECT id FROM theses WHERE experiment_id = %s ORDER BY id DESC LIMIT 1", (exp,)
    ).fetchone()

    new_trades = 0
    for t in result.trades[n_persisted:]:
        order_id = t.order_id
        if t.forced:
            order = Order(
                experiment_id=exp,
                thesis_id=thesis_row[0] if thesis_row else None,
                symbol=t.symbol, side=t.side,
                status=OrderStatus.FORCED_LIQUIDATION,
                strategy_tag=t.strategy_tag,
                proposed_qty=t.qty, approved_qty=None, human_rationale=None,
                proposed_at=clock.session_open_utc(t.session),
                created_at=clock.session_open_utc(t.session),
            )
            repo.insert(conn, order)
            order_id = order.id
        row = Trade(
            order_id=order_id, symbol=t.symbol, side=t.side, qty=t.qty,
            fill_price=t.fill_price, slippage_applied=t.slippage_applied,
            strategy_tag=t.strategy_tag, exit_type=t.exit_type,
            executed_at=clock.session_open_utc(t.session),
            created_at=clock.session_open_utc(t.session),
        )
        repo.insert(conn, row)
        seq_to_id[t.trade_seq] = row.id
        new_trades += 1

    # Lots: positional prefix mapping (replay determinism keeps lot order
    # stable); first N persisted rows pair with the replay's first N lots.
    persisted_lot_ids = [
        r[0] for r in conn.execute(
            "SELECT l.id FROM tax_lots l JOIN trades t ON l.opening_trade_id = t.id"
            " JOIN orders o ON t.order_id = o.id WHERE o.experiment_id = %s"
            " ORDER BY l.id", (exp,),
        )
    ]
    updated = 0
    for i, lot in enumerate(result.lots):
        values = dict(
            opening_trade_id=seq_to_id[lot.opening_trade_seq],
            closing_trade_id=seq_to_id[lot.closing_trade_seq] if lot.closing_trade_seq else None,
            symbol=lot.symbol, qty=lot.qty, cost_basis=lot.cost_basis,
            acquisition_date=lot.acquisition_date,
            closed_at=clock.session_open_utc(lot.closed_session) if lot.closed_session else None,
            realized_pnl=lot.realized_pnl,
            holding_period_days=lot.holding_period_days,
            wash_sale_candidate=lot.wash_sale_candidate,
        )
        if i < len(persisted_lot_ids):
            changed = conn.execute(
                "UPDATE tax_lots SET closing_trade_id = %(closing_trade_id)s,"
                " qty = %(qty)s, cost_basis = %(cost_basis)s, closed_at = %(closed_at)s,"
                " realized_pnl = %(realized_pnl)s, holding_period_days = %(holding_period_days)s,"
                " wash_sale_candidate = %(wash_sale_candidate)s"
                " WHERE id = %(id)s AND (closing_trade_id IS DISTINCT FROM %(closing_trade_id)s"
                "  OR qty IS DISTINCT FROM %(qty)s"
                "  OR wash_sale_candidate IS DISTINCT FROM %(wash_sale_candidate)s)",
                {**values, "id": persisted_lot_ids[i]},
            ).rowcount
            updated += changed
        else:
            repo.insert(conn, TaxLot(**values))

    for nav in result.navs:
        conn.execute(
            "INSERT INTO daily_nav (experiment_id, nav_date, nav, cash, positions_value,"
            " contains_forced_resolution) VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (experiment_id, nav_date) DO UPDATE SET nav = EXCLUDED.nav,"
            " cash = EXCLUDED.cash, positions_value = EXCLUDED.positions_value,"
            " contains_forced_resolution = EXCLUDED.contains_forced_resolution",
            (exp, nav.session, nav.nav, nav.cash, nav.positions_value,
             nav.contains_forced_resolution),
        )
    for b in result.benchmark_navs:
        conn.execute(
            "INSERT INTO benchmarks_nav (experiment_id, benchmark_name, nav_date, nav,"
            " contains_forced_resolution) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (experiment_id, benchmark_name, nav_date) DO UPDATE SET"
            " nav = EXCLUDED.nav, contains_forced_resolution = EXCLUDED.contains_forced_resolution",
            (exp, b.benchmark_name, b.session, b.nav, b.contains_forced_resolution),
        )
    for s in result.position_snapshots:
        conn.execute(
            "INSERT INTO positions (experiment_id, symbol, qty, avg_cost, market_value,"
            " frozen, frozen_mark, as_of) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (experiment_id, symbol, as_of) DO UPDATE SET qty = EXCLUDED.qty,"
            " avg_cost = EXCLUDED.avg_cost, market_value = EXCLUDED.market_value,"
            " frozen = EXCLUDED.frozen, frozen_mark = EXCLUDED.frozen_mark",
            (exp, s.symbol, s.qty, s.avg_cost, s.market_value, s.frozen, s.frozen_mark,
             clock.session_close_utc(s.session)),
        )
    for inc in result.incidents:
        conn.execute(
            "INSERT INTO data_incidents (experiment_id, incident_class, status, source_name,"
            " symbol, description, detected_at, resolved_at, resolution_assertion_id)"
            " VALUES (%s, %s::incident_class, %s::incident_status, 'engine', %s, %s, %s, %s, %s)"
            " ON CONFLICT (experiment_id, symbol, incident_class, detected_at)"
            " WHERE source_name = 'engine' DO UPDATE SET status = EXCLUDED.status,"
            " resolved_at = EXCLUDED.resolved_at,"
            " resolution_assertion_id = EXCLUDED.resolution_assertion_id",
            (exp, inc.incident_class.value,
             {"OPEN": "OPEN", "RESOLVED": "RESOLVED_HUMAN",
              "FORCED": "FORCED_LIQUIDATION"}[inc.phase.value],
             inc.symbol, inc.description,
             clock.session_open_utc(inc.detected_session),
             clock.session_close_utc(inc.resolved_session) if inc.resolved_session else None,
             inc.resolution_assertion_id),
        )

    m_hash = persist.manifest_hash(manifest)
    r_hash = persist.result_hash(result)
    existing = conn.execute(
        "SELECT result_hash FROM simulation_runs WHERE manifest_hash = %s", (m_hash,)
    ).fetchone()
    if existing is not None:
        if existing[0] != r_hash:
            raise persist.DeterminismBreach(
                f"manifest {m_hash[:12]} previously produced {existing[0][:12]}, now {r_hash[:12]}"
            )
    else:
        from dataclasses import asdict
        conn.execute(
            "INSERT INTO simulation_runs (experiment_id, manifest, manifest_hash,"
            " result_hash, code_commit, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (exp, persist.canonical_json(asdict(manifest)), m_hash, r_hash,
             manifest.code_commit,
             clock.session_close_utc(result.navs[-1].session) if result.navs
             else clock.session_close_utc(manifest.session_end)),
        )
    return new_trades, updated


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run(conn, as_of: date, commit: bool = True) -> LiveRunSummary:
    """One live-runner pass targeting `as_of` (the just-closed session).

    commit=False lets tests run inside a rollback transaction."""
    conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
    try:
        exp = live_experiment(conn)
        cfg = persist.engine_config_from_yaml()

        if as_of < GENESIS:
            pins = restatement_guard(conn, exp.id, None)
            rebuild_price_bars(conn, pins or None)
            completeness_gate(conn, active_universe(cfg), as_of)
            return LiveRunSummary(as_of, 0, 0, 0, cfg.initial_cash, False,
                                  tuple(sorted(pins)), dry_run=True)

        last_live = conn.execute(
            "SELECT max(nav_date) FROM daily_nav WHERE experiment_id = %s", (exp.id,)
        ).fetchone()[0]
        pins = restatement_guard(conn, exp.id, last_live)
        rebuild_price_bars(conn, pins or None)
        completeness_gate(conn, active_universe(cfg), as_of)

        manifest = persist.build_manifest(conn, exp.id, GENESIS, as_of, cfg)
        result = simulate(persist.load_input(conn, manifest, cfg))

        persisted = persisted_live_trades(conn, exp.id)
        try:
            seq_to_id = verify_prefix(persisted, result)
        except PrefixBreach as e:
            raise_incident(
                conn, incident_class=IncidentClass.INCONSISTENT,
                source_name="engine", symbol=None, description=str(e),
            )
            if commit:
                conn.commit()
            notify("PREFIX BREACH — ledger halted", str(e), priority="urgent",
                   tags="rotating_light")
            raise

        new_trades, updated_lots = persist_incremental(
            conn, result, manifest, seq_to_id, len(persisted)
        )
        if commit:
            conn.commit()
        return LiveRunSummary(
            session=as_of,
            replayed_sessions=len(result.navs),
            persisted_new_trades=new_trades,
            updated_lots=updated_lots,
            nav=result.navs[-1].nav if result.navs else cfg.initial_cash,
            tainted=result.navs[-1].contains_forced_resolution if result.navs else False,
            pinned_symbols=tuple(sorted(pins)),
            dry_run=False,
        )
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
