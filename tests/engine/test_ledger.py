"""Ledger unit + property tests: FIFO, lot splitting, wash-sale candidates."""

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from analytics.engine.ledger import (
    LedgerError,
    apply_buy,
    apply_fill,
    apply_sell,
    flag_wash_sale_candidates,
)
from analytics.engine.state import TradeRow
from schemas.enums import OrderSide

D = Decimal


def trade(seq: int, side: OrderSide, qty: str, price: str, day: date, symbol="AAA") -> TradeRow:
    return TradeRow(
        trade_seq=seq, order_id=seq, symbol=symbol, side=side, qty=D(qty),
        fill_price=D(price), slippage_applied=D("0"), strategy_tag="t", session=day,
    )


def test_fifo_order_and_realized_pnl():
    d1, d2, d3 = date(2026, 1, 5), date(2026, 2, 5), date(2026, 3, 5)
    r1 = apply_buy((), trade(1, OrderSide.BUY, "10", "100", d1), 1)
    r2 = apply_buy(r1.open_lots, trade(2, OrderSide.BUY, "10", "110", d2), r1.next_lot_seq)
    # Sell 15 @ 120: closes lot1 (10 @ 100) fully + 5 of lot2 (@110)
    r3 = apply_sell(r2.open_lots, trade(3, OrderSide.SELL, "15", "120", d3), r2.next_lot_seq)
    assert r3.realized_pnl == D("15") * D("120") - (D("10") * D("100") + D("5") * D("110"))
    assert len(r3.open_lots) == 1 and r3.open_lots[0].qty == D("5")
    assert r3.open_lots[0].cost_basis == D("550.00")
    closed = r3.new_lot_rows
    assert [c.qty for c in closed] == [D("10"), D("5")]
    assert closed[0].holding_period_days == (d3 - d1).days


def test_partial_close_splits_lot_preserving_opening_trade():
    d1, d2 = date(2026, 1, 5), date(2026, 1, 20)
    r1 = apply_buy((), trade(1, OrderSide.BUY, "10", "100", d1), 1)
    r2 = apply_sell(r1.open_lots, trade(2, OrderSide.SELL, "4", "90", d2), r1.next_lot_seq)
    assert r2.new_lot_rows[0].opening_trade_seq == 1
    assert r2.open_lots[0].opening_trade_seq == 1
    assert r2.open_lots[0].qty == D("6")
    assert r2.new_lot_rows[0].realized_pnl == D("4") * D("90") - D("400.00")


def test_oversell_raises():
    r1 = apply_buy((), trade(1, OrderSide.BUY, "10", "100", date(2026, 1, 5)), 1)
    with pytest.raises(LedgerError):
        apply_sell(r1.open_lots, trade(2, OrderSide.SELL, "11", "100", date(2026, 1, 6)), 2)


def test_wash_sale_candidate_flagged():
    d_loss, d_rebuy = date(2026, 1, 20), date(2026, 2, 5)  # 16 days apart
    trades = [
        trade(1, OrderSide.BUY, "10", "100", date(2026, 1, 5)),
        trade(2, OrderSide.SELL, "10", "80", d_loss),      # realized loss
        trade(3, OrderSide.BUY, "10", "82", d_rebuy),      # repurchase in window
    ]
    r1 = apply_buy((), trades[0], 1)
    r2 = apply_sell(r1.open_lots, trades[1], r1.next_lot_seq)
    rows = {row.lot_seq: row for row in r1.new_lot_rows + r2.new_lot_rows}
    flag_wash_sale_candidates(rows, trades)
    loss_row = next(r for r in rows.values() if r.realized_pnl is not None)
    assert loss_row.wash_sale_candidate is True


def test_wash_sale_not_flagged_outside_window():
    trades = [
        trade(1, OrderSide.BUY, "10", "100", date(2026, 1, 5)),
        trade(2, OrderSide.SELL, "10", "80", date(2026, 1, 20)),
        trade(3, OrderSide.BUY, "10", "82", date(2026, 3, 15)),  # 54 days later
    ]
    r1 = apply_buy((), trades[0], 1)
    r2 = apply_sell(r1.open_lots, trades[1], r1.next_lot_seq)
    rows = {row.lot_seq: row for row in r1.new_lot_rows + r2.new_lot_rows}
    flag_wash_sale_candidates(rows, trades)
    loss_row = next(r for r in rows.values() if r.realized_pnl is not None)
    assert loss_row.wash_sale_candidate is False


@given(
    buys=st.lists(
        st.tuples(
            st.decimals(min_value="1", max_value="100", places=0),
            st.decimals(min_value="1", max_value="500", places=2),
        ),
        min_size=1, max_size=8,
    ),
    sell_fraction=st.integers(min_value=1, max_value=100),
    sell_price=st.decimals(min_value="1", max_value="500", places=2),
)
def test_property_conservation(buys, sell_fraction, sell_price):
    """Cash out + realized P&L + remaining basis == cash in (conservation);

    lots never negative."""
    lots: tuple = ()
    seq = 1
    total_bought_qty = D(0)
    total_cost = D(0)
    day = date(2026, 1, 5)
    for i, (qty, price) in enumerate(buys):
        t = trade(i + 1, OrderSide.BUY, str(qty), str(price), day)
        r = apply_buy(lots, t, seq)
        lots, seq = r.open_lots, r.next_lot_seq
        total_bought_qty += qty
        total_cost += -r.cash_delta

    sell_qty = (total_bought_qty * sell_fraction / 100).quantize(D("1"))
    if sell_qty == 0:
        return
    t = trade(99, OrderSide.SELL, str(sell_qty), str(sell_price), date(2026, 2, 5))
    r = apply_sell(lots, t, seq)
    remaining_basis = sum((lot.cost_basis for lot in r.open_lots), D(0))
    closed_basis = sum((row.cost_basis for row in r.new_lot_rows), D(0))
    assert all(lot.qty > 0 for lot in r.open_lots)
    assert remaining_basis + closed_basis == total_cost
    assert r.cash_delta - r.realized_pnl == closed_basis
