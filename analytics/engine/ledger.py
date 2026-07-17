"""FIFO lot engine (pure). Lot-selection rule (DECISIONS.md): FIFO; a partial

close splits the lot — the closed portion becomes its own LotRow sharing the
opening_trade_seq; the remainder stays open with proportional basis. Wash-sale
CANDIDATE flag only (±30 calendar days) — not tax-authoritative.
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal

from analytics.engine.state import CENT, LotRow, OpenLot, TradeRow
from schemas.enums import OrderSide


class LedgerError(Exception):
    pass


@dataclass(frozen=True)
class FillResult:
    open_lots: tuple[OpenLot, ...]
    new_lot_rows: tuple[LotRow, ...]      # rows created/closed by this fill
    realized_pnl: Decimal                 # 0 for buys
    cash_delta: Decimal                   # negative for buys, positive for sells
    next_lot_seq: int


def apply_buy(
    open_lots: tuple[OpenLot, ...], trade: TradeRow, next_lot_seq: int
) -> FillResult:
    cost = (trade.qty * trade.fill_price).quantize(CENT)
    lot = OpenLot(
        lot_seq=next_lot_seq,
        symbol=trade.symbol,
        qty=trade.qty,
        cost_basis=cost,
        acquisition_date=trade.session,
        opening_trade_seq=trade.trade_seq,
    )
    row = LotRow(
        lot_seq=lot.lot_seq,
        opening_trade_seq=trade.trade_seq,
        symbol=trade.symbol,
        qty=trade.qty,
        cost_basis=cost,
        acquisition_date=trade.session,
    )
    return FillResult(
        open_lots=open_lots + (lot,),
        new_lot_rows=(row,),
        realized_pnl=Decimal(0),
        cash_delta=-cost,
        next_lot_seq=next_lot_seq + 1,
    )


def apply_sell(
    open_lots: tuple[OpenLot, ...], trade: TradeRow, next_lot_seq: int
) -> FillResult:
    """FIFO close. Splits the last-touched lot on partial close."""
    remaining = trade.qty
    proceeds_total = Decimal(0)
    realized_total = Decimal(0)
    closed_rows: list[LotRow] = []
    surviving: list[OpenLot] = []
    seq = next_lot_seq

    for lot in open_lots:  # open_lots kept in acquisition (FIFO) order
        if remaining <= 0 or lot.symbol != trade.symbol:
            surviving.append(lot)
            continue
        close_qty = min(lot.qty, remaining)
        remaining -= close_qty
        proceeds = (close_qty * trade.fill_price).quantize(CENT)
        basis = (lot.cost_basis * close_qty / lot.qty).quantize(CENT)
        pnl = proceeds - basis
        proceeds_total += proceeds
        realized_total += pnl
        closed_rows.append(
            LotRow(
                lot_seq=lot.lot_seq if close_qty == lot.qty else seq,
                opening_trade_seq=lot.opening_trade_seq,
                symbol=lot.symbol,
                qty=close_qty,
                cost_basis=basis,
                acquisition_date=lot.acquisition_date,
                closing_trade_seq=trade.trade_seq,
                closed_session=trade.session,
                realized_pnl=pnl,
                holding_period_days=(trade.session - lot.acquisition_date).days,
            )
        )
        if close_qty < lot.qty:  # split: remainder stays open, keeps its lot_seq
            seq += 1
            surviving.append(
                replace(lot, qty=lot.qty - close_qty, cost_basis=lot.cost_basis - basis)
            )

    if remaining > 0:
        raise LedgerError(
            f"sell of {trade.qty} {trade.symbol} exceeds open lots by {remaining}"
        )
    return FillResult(
        open_lots=tuple(surviving),
        new_lot_rows=tuple(closed_rows),
        realized_pnl=realized_total,
        cash_delta=proceeds_total,
        next_lot_seq=seq,
    )


def apply_fill(
    open_lots: tuple[OpenLot, ...], trade: TradeRow, next_lot_seq: int
) -> FillResult:
    if trade.side is OrderSide.BUY:
        return apply_buy(open_lots, trade, next_lot_seq)
    return apply_sell(open_lots, trade, next_lot_seq)


def flag_wash_sale_candidates(
    lot_rows: dict[int, LotRow], trades: list[TradeRow]
) -> None:
    """Mark closed-at-a-loss lots whose symbol was repurchased within ±30

    calendar days of the loss sale. Mutates the accumulator dict in place
    (rows are frozen; replaced wholesale). CANDIDATE only."""
    buys_by_symbol: dict[str, list[date]] = {}
    for t in trades:
        if t.side is OrderSide.BUY:
            buys_by_symbol.setdefault(t.symbol, []).append(t.session)
    for seq, row in list(lot_rows.items()):
        if row.realized_pnl is None or row.realized_pnl >= 0 or row.closed_session is None:
            continue
        window_lo = row.closed_session - timedelta(days=30)
        window_hi = row.closed_session + timedelta(days=30)
        for buy_day in buys_by_symbol.get(row.symbol, ()):
            if window_lo <= buy_day <= window_hi and buy_day != row.acquisition_date:
                lot_rows[seq] = replace(row, wash_sale_candidate=True)
                break
