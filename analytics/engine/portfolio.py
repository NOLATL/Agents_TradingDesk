"""Corporate actions + mark-to-market (pure).

Supported actions (spec §6): splits (splitFactor != 1 adjusts share counts and
open-lot quantities on ex-date; basis unchanged) and ordinary cash dividends
(divCash credits cash on ex-date per held share — pay-date modeling deferred,
DECISIONS.md). Anything else is NOT computed here — detection of unsupported
patterns happens in quarantine.py.

MTM: frozen positions mark at frozen_mark (§6 freeze-and-mark); everything
else at the session close. The accounting identity nav == cash +
positions_value is asserted at every session by the loop.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from analytics.engine.bars import bar_for, last_valid_close
from analytics.engine.state import (
    CENT,
    SHARE_QUANTUM,
    BarMatrix,
    NavRow,
    PortfolioState,
    PositionSnapshot,
    PositionState,
)


def apply_corporate_actions(
    state: PortfolioState, session: date, bars: BarMatrix
) -> tuple[PortfolioState, Decimal]:
    """Apply splits + dividends for held symbols with a bar this session.

    Returns (new_state, dividend_cash_credited)."""
    dividend_cash = Decimal(0)
    new_positions: list[PositionState] = []
    for pos in state.positions:  # already sorted by symbol
        bar = bar_for(bars, pos.symbol, session)
        if bar is None or pos.qty == 0 or pos.frozen:
            new_positions.append(pos)
            continue
        updated = pos
        if bar.div_cash and bar.div_cash != 0:
            dividend_cash += (pos.qty * bar.div_cash).quantize(CENT)
        if bar.split_factor and bar.split_factor != 1:
            factor = bar.split_factor
            updated = replace(
                updated,
                qty=(pos.qty * factor).quantize(SHARE_QUANTUM),
                open_lots=tuple(
                    replace(lot, qty=(lot.qty * factor).quantize(SHARE_QUANTUM))
                    for lot in pos.open_lots
                ),
            )
        new_positions.append(updated)
    return (
        replace(
            state,
            positions=tuple(new_positions),
            cash=(state.cash + dividend_cash).quantize(CENT),
        ),
        dividend_cash,
    )


def position_value(pos: PositionState, session: date, bars: BarMatrix) -> Decimal:
    if pos.qty == 0:
        return Decimal(0)
    if pos.frozen:
        if pos.frozen_mark is None:
            raise ValueError(f"frozen position {pos.symbol} without a mark")
        return (pos.qty * pos.frozen_mark).quantize(CENT)
    lvc = last_valid_close(bars, pos.symbol, session)
    if lvc is None:
        raise ValueError(f"no price history for held position {pos.symbol} by {session}")
    return (pos.qty * lvc[1]).quantize(CENT)


def mark_to_market(
    state: PortfolioState, session: date, bars: BarMatrix
) -> tuple[NavRow, list[PositionSnapshot]]:
    positions_value = Decimal(0)
    snapshots: list[PositionSnapshot] = []
    for pos in state.positions:
        value = position_value(pos, session, bars)
        positions_value += value
        if pos.qty != 0:
            basis = sum((lot.cost_basis for lot in pos.open_lots), Decimal(0))
            avg_cost = (basis / pos.qty).quantize(CENT) if pos.qty else Decimal(0)
            snapshots.append(
                PositionSnapshot(
                    session=session,
                    symbol=pos.symbol,
                    qty=pos.qty,
                    avg_cost=avg_cost,
                    market_value=value,
                    frozen=pos.frozen,
                    frozen_mark=pos.frozen_mark,
                )
            )
    nav = (state.cash + positions_value).quantize(CENT)
    return (
        NavRow(
            session=session,
            nav=nav,
            cash=state.cash.quantize(CENT),
            positions_value=positions_value.quantize(CENT),
            contains_forced_resolution=state.tainted,
        ),
        snapshots,
    )
