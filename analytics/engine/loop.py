"""The simulation loop: `simulate(SimulationInput) -> SimulationResult`.

PURE: no DB, no wall clock, no randomness, no config files — everything
arrives in the input, everything leaves in the result (enforced by
tests/engine/test_purity.py). Phase order per session:

  1. corporate actions (ex-date, at open)
  2. quarantine tick (detect → freeze; resolutions; timeouts → forced liq.)
  3. fills of approved orders due this session (gated by breakers/freezes)
  4. mark-to-market at close → DailyNav (identity nav == cash + positions
     asserted every session)
  5. control benchmarks (same fill/mark code paths)
  6. breakers (EOD-only; gate the NEXT session's fills)

Wash-sale candidates are flagged once at end over the full trade record.
"""

from dataclasses import replace
from decimal import Decimal, localcontext

from analytics import volatility
from analytics.engine import breakers as breakers_mod
from analytics.engine import quarantine as quarantine_mod
from analytics.engine.bars import bar_for, trailing_adj_closes
from analytics.engine.controls import BenchmarkState, step_benchmark
from analytics.engine.execution import fill_price, orders_due
from analytics.engine.ledger import apply_fill, flag_wash_sale_candidates
from analytics.engine.portfolio import apply_corporate_actions, mark_to_market
from analytics.engine.state import (
    SHARE_QUANTUM,
    ApprovedOrder,
    BreakerEvent,
    BreakerState,
    OpenLot,
    PortfolioState,
    PositionState,
    QuarantinePhase,
    ResultAccumulator,
    SimulationInput,
    SimulationResult,
    TradeRow,
)
from schemas.enums import OrderSide

BENCHMARK_NAMES = ("spy", "equal_weight_sp500_proxy", "momentum_top10",
                   "same_basket_no_llm_timing")

VOL_WINDOW = 60


class AccountingIdentityError(Exception):
    pass


def _annualized_vol(inp: SimulationInput, symbol: str, session) -> Decimal | None:
    closes = trailing_adj_closes(inp.bars, symbol, session, VOL_WINDOW)
    if len(closes) < 10:
        return None
    return Decimal(str(round(volatility.annualized_volatility(closes), 6)))


def _upsert_position(positions: tuple[PositionState, ...], updated: PositionState
                     ) -> tuple[PositionState, ...]:
    out = [p for p in positions if p.symbol != updated.symbol]
    if updated.qty != 0 or updated.frozen:
        out.append(updated)
    return tuple(sorted(out, key=lambda p: p.symbol))


def _execute_trade(state: PortfolioState, trade: TradeRow, acc: ResultAccumulator
                   ) -> PortfolioState:
    pos = state.position(trade.symbol) or PositionState(trade.symbol, Decimal(0), ())
    result = apply_fill(pos.open_lots, trade, state.next_lot_seq)
    for row in result.new_lot_rows:
        acc.lots[row.lot_seq] = row
    new_qty = (pos.qty + trade.qty if trade.side is OrderSide.BUY
               else pos.qty - trade.qty).quantize(SHARE_QUANTUM)
    acc.trades.append(trade)
    return replace(
        state,
        cash=state.cash + result.cash_delta,
        positions=_upsert_position(
            state.positions, replace(pos, qty=new_qty, open_lots=result.open_lots)
        ),
        next_trade_seq=trade.trade_seq + 1,
        next_lot_seq=result.next_lot_seq,
    )


def _fill_orders(state: PortfolioState, session, inp: SimulationInput,
                 acc: ResultAccumulator) -> PortfolioState:
    for order in orders_due(inp.approved_orders, session):
        gate_reason = None
        if state.breakers.data_integrity_tripped or state.breakers.operational_tripped:
            gate_reason = "halting breaker active"
        elif state.breakers.market_risk_tripped and order.side is OrderSide.BUY:
            gate_reason = "market-risk breaker blocks new risk"
        else:
            pos = state.position(order.symbol)
            if pos is not None and pos.frozen:
                gate_reason = "symbol frozen (quarantine)"
        bar = bar_for(inp.bars, order.symbol, session)
        if gate_reason is None and bar is None:
            gate_reason = "no bar for session"
        if gate_reason:
            acc.breaker_events.append(
                BreakerEvent(session, "order_gate", True,
                             f"order {order.order_id} ({order.side.value} {order.symbol}) "
                             f"skipped: {gate_reason}")
            )
            continue
        vol = _annualized_vol(inp, order.symbol, session)
        price, slip = fill_price(bar, order.side, vol, inp.config)
        trade = TradeRow(
            trade_seq=state.next_trade_seq,
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            fill_price=price,
            slippage_applied=slip,
            strategy_tag=order.strategy_tag,
            session=session,
            exit_type=order.exit_type,
            forced=order.forced,
        )
        state = _execute_trade(state, trade, acc)
    return state


def _quarantine_tick(state: PortfolioState, session, inp: SimulationInput,
                     acc: ResultAccumulator) -> PortfolioState:
    new_cases = quarantine_mod.detect(state, session, inp.bars, inp.sessions)
    state = quarantine_mod.freeze(state, new_cases, session, inp.bars,
                                  inp.config.quarantine_window_sessions)

    for case, res in quarantine_mod.resolutions_due(state, session, inp.resolutions):
        state = quarantine_mod.apply_resolution(state, case, res, session)

    for case in quarantine_mod.timeouts_due(state, session):
        pos = state.position(case.symbol)
        if pos is not None and pos.qty > 0:
            trade = TradeRow(
                trade_seq=state.next_trade_seq,
                order_id=0,                      # persist synthesizes FORCED_LIQUIDATION order
                symbol=case.symbol,
                side=OrderSide.SELL,
                qty=pos.qty,
                fill_price=pos.frozen_mark or Decimal(0),
                slippage_applied=Decimal(0),     # a mark, not a market fill (§6)
                strategy_tag="quarantine_forced",
                session=session,
                forced=True,
            )
            state = _execute_trade(state, trade, acc)
        state = replace(
            state,
            tainted=True,                        # taint-forward (§6, DECISIONS.md)
            positions=tuple(p for p in state.positions if p.symbol != case.symbol),
            quarantine=tuple(
                replace(c, phase=QuarantinePhase.FORCED, resolved_session=session)
                if c.symbol == case.symbol and c.phase is QuarantinePhase.OPEN else c
                for c in state.quarantine
            ),
        )
    return state


def simulate(inp: SimulationInput) -> SimulationResult:
    with localcontext() as ctx:
        ctx.prec = 28
        acc = ResultAccumulator()
        state = PortfolioState(
            cash=inp.config.initial_cash,
            positions=(),
            quarantine=(),
            breakers=BreakerState(high_water_mark=inp.config.initial_cash),
        )
        benchmarks = {
            name: BenchmarkState(name=name, cash=inp.config.initial_cash)
            for name in BENCHMARK_NAMES
        }

        for session in inp.sessions:
            state, _dividends = apply_corporate_actions(state, session, inp.bars)
            state = _quarantine_tick(state, session, inp, acc)
            state = _fill_orders(state, session, inp, acc)

            nav_row, snapshots = mark_to_market(state, session, inp.bars)
            if nav_row.nav != nav_row.cash + nav_row.positions_value:
                raise AccountingIdentityError(f"{session}: {nav_row}")
            acc.navs.append(nav_row)
            acc.position_snapshots.extend(snapshots)

            for name in BENCHMARK_NAMES:
                benchmarks[name], bench_nav = step_benchmark(
                    benchmarks[name], session, inp.bars, inp.config
                )
                acc.benchmark_navs.append(bench_nav)

            open_cases = sum(
                1 for c in state.quarantine if c.phase is QuarantinePhase.OPEN
            )
            new_breakers, events = breakers_mod.evaluate(
                state.breakers, session, nav_row, open_cases,
                inp.config.drawdown_breaker_pct,
            )
            state = replace(state, breakers=new_breakers)
            acc.breaker_events.extend(events)

        flag_wash_sale_candidates(acc.lots, acc.trades)
        return SimulationResult(
            experiment_id=inp.experiment_id,
            trades=tuple(acc.trades),
            lots=tuple(acc.lots[k] for k in sorted(acc.lots)),
            navs=tuple(acc.navs),
            benchmark_navs=tuple(acc.benchmark_navs),
            position_snapshots=tuple(acc.position_snapshots),
            incidents=tuple(quarantine_mod.incident_rows(state)),
            breaker_events=tuple(acc.breaker_events),
        )
