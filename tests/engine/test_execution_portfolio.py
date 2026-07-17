"""Execution fill-model and portfolio/MTM scenario tests."""

from datetime import date
from decimal import Decimal

from analytics.engine import clock
from analytics.engine.execution import due_session, fill_price, slippage_bps
from analytics.engine.portfolio import apply_corporate_actions, mark_to_market
from analytics.engine.state import (
    OpenLot,
    PortfolioState,
    PositionState,
    BreakerState,
)
from schemas.enums import OrderSide
from tests.engine.factories import approval_before, make_bars, make_config, sessions

D = Decimal


def bar(price: str, split: str = "1", div: str = "0"):
    day = date(2026, 7, 6)
    return make_bars("AAA", [day], [price], splits={day: split}, dividends={day: div})[day]


def test_slippage_direction_and_tiering():
    cfg = make_config()
    b = bar("100")
    buy_price, buy_slip = fill_price(b, OrderSide.BUY, None, cfg)
    sell_price, sell_slip = fill_price(b, OrderSide.SELL, None, cfg)
    assert buy_price == D("100.10") and sell_price == D("99.90")  # 10bps default
    assert buy_slip == sell_slip == D("0.10")
    # High-vol tier: 35 bps
    hi_price, _ = fill_price(b, OrderSide.BUY, D("0.65"), cfg)
    assert hi_price == D("100.35")
    assert slippage_bps(D("0.45"), cfg.slippage_tiers, cfg.default_slippage_bps) == D("20")


def test_orders_fill_next_open_never_same_bar():
    day = date(2026, 7, 8)
    order = approval_before(day, 1, "AAA", OrderSide.BUY, "10")
    assert due_session(order) == day
    # An order decided DURING a session fills the following session.
    from analytics.engine.state import ApprovedOrder
    midday = clock.session_open_utc(day).replace(hour=15)
    intraday = ApprovedOrder(order_id=2, symbol="AAA", side=OrderSide.BUY,
                             qty=D("1"), decided_at=midday, strategy_tag="t")
    assert due_session(intraday) == clock.next_session(day)


def _portfolio_with_position(qty="10", basis="1000.00", frozen=False, mark=None):
    pos = PositionState(
        symbol="AAA", qty=D(qty),
        open_lots=(OpenLot(1, "AAA", D(qty), D(basis), date(2026, 6, 1), 1),),
        frozen=frozen, frozen_mark=D(mark) if mark else None,
    )
    return PortfolioState(cash=D("5000.00"), positions=(pos,),
                          quarantine=(), breakers=BreakerState())


def test_split_adjusts_qty_not_basis():
    day = date(2026, 7, 6)
    bars = {"AAA": make_bars("AAA", [day], ["50"], splits={day: "2"})}
    state = _portfolio_with_position(qty="10", basis="1000.00")
    new_state, div = apply_corporate_actions(state, day, bars)
    pos = new_state.position("AAA")
    assert pos.qty == D("20.0000")
    assert pos.open_lots[0].qty == D("20.0000")
    assert pos.open_lots[0].cost_basis == D("1000.00")  # basis unchanged
    assert div == 0


def test_dividend_credits_cash_on_ex_date():
    day = date(2026, 7, 6)
    bars = {"AAA": make_bars("AAA", [day], ["100"], dividends={day: "1.50"})}
    state = _portfolio_with_position(qty="10")
    new_state, div = apply_corporate_actions(state, day, bars)
    assert div == D("15.00")
    assert new_state.cash == D("5015.00")


def test_mtm_identity_and_frozen_mark():
    day = date(2026, 7, 6)
    bars = {"AAA": make_bars("AAA", [day], ["120"])}
    state = _portfolio_with_position(qty="10")
    nav, snaps = mark_to_market(state, day, bars)
    assert nav.positions_value == D("1200.00")
    assert nav.nav == nav.cash + nav.positions_value  # the identity
    assert snaps[0].avg_cost == D("100.00")

    frozen_state = _portfolio_with_position(qty="10", frozen=True, mark="95")
    nav_frozen, _ = mark_to_market(frozen_state, day, bars)
    assert nav_frozen.positions_value == D("950.00")  # frozen_mark, not live 120
