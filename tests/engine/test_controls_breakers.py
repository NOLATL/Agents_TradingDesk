"""Gate B: controls run on identical timing/cost assumptions; breakers behave

per spec §8 (EOD-only, block-vs-halt, reset conditions)."""

from datetime import date
from decimal import Decimal

from analytics.engine import breakers as breakers_mod
from analytics.engine.controls import BenchmarkState, _momentum_ranking, step_benchmark
from analytics.engine.execution import fill_price
from analytics.engine.loop import simulate
from analytics.engine.state import BreakerState, NavRow
from schemas.enums import OrderSide
from tests.engine.factories import (
    approval_before,
    make_bars,
    make_config,
    make_input,
    sessions,
)

D = Decimal
START = date(2026, 1, 5)


def test_benchmark_fills_route_through_the_same_fill_model():
    """The spy control's entry cost equals the live fill model's price for the

    same bar — identical cost assumptions, verified not asserted."""
    days = sessions(START, 3)
    cfg = make_config()
    bars = {"SPY": make_bars("SPY", days, ["400", "401", "402"]),
            "RSP": make_bars("RSP", days, ["150", "151", "152"])}
    bstate = BenchmarkState(name="spy", cash=cfg.initial_cash)
    bstate, nav0 = step_benchmark(bstate, days[0], bars, cfg)
    expected_price, _ = fill_price(bars["SPY"][days[0]], OrderSide.BUY, None, cfg)
    qty = bstate.holdings[0][1]
    spent = cfg.initial_cash - bstate.cash
    assert spent == (qty * expected_price).quantize(D("0.01"))


def test_benchmarks_step_every_session_alongside_live_book():
    days = sessions(START, 6)
    bars = {
        "AAA": make_bars("AAA", days, ["100"] * 6),
        "SPY": make_bars("SPY", days, ["400"] * 6),
        "RSP": make_bars("RSP", days, ["150"] * 6),
    }
    result = simulate(make_input(bars, days,
                                 [approval_before(days[1], 1, "AAA", OrderSide.BUY, "5")]))
    by_name = {}
    for row in result.benchmark_navs:
        by_name.setdefault(row.benchmark_name, []).append(row.session)
    assert set(by_name) == {"spy", "equal_weight_sp500_proxy", "momentum_top10",
                            "same_basket_no_llm_timing"}
    for name, session_list in by_name.items():
        assert session_list == list(days), name  # identical timing: every session


def test_momentum_ranking_deterministic_with_tiebreak():
    days = sessions(START, 60)
    bars = {
        "AAA": make_bars("AAA", days, [str(100 + i) for i in range(60)]),      # strong
        "BBB": make_bars("BBB", days, [str(100 + i // 2) for i in range(60)]), # medium
        "CCC": make_bars("CCC", days, ["100"] * 60),                            # flat
    }
    top = _momentum_ranking(("AAA", "BBB", "CCC"), days[-1], bars, 2)
    assert top == ["AAA", "BBB"]
    # Tie-break: identical series rank alphabetically — deterministic
    tie = _momentum_ranking(("YYY", "XXX"), days[-1],
                            {"XXX": bars["CCC"], "YYY": bars["CCC"]}, 2)
    assert tie == ["XXX", "YYY"]


def nav(session, nav_val, tainted=False):
    return NavRow(session=session, nav=D(nav_val), cash=D(nav_val),
                  positions_value=D("0"), contains_forced_resolution=tainted)


def test_market_risk_breaker_trips_and_resets_on_new_hwm():
    cfg_dd = D("15")
    s = sessions(START, 4)
    b0 = BreakerState(high_water_mark=D("10000"))
    b1, ev1 = breakers_mod.evaluate(b0, s[0], nav(s[0], "8000"), 0, cfg_dd)  # -20% dd
    assert b1.market_risk_tripped and ev1[0].breaker == "market_risk"
    # Partial recovery below HWM: stays tripped (reset only on new HWM)
    b2, _ = breakers_mod.evaluate(b1, s[1], nav(s[1], "9500"), 0, cfg_dd)
    assert b2.market_risk_tripped
    # New high-water mark: resets
    b3, ev3 = breakers_mod.evaluate(b2, s[2], nav(s[2], "10500"), 0, cfg_dd)
    assert not b3.market_risk_tripped and b3.high_water_mark == D("10500")


def test_market_risk_blocks_buys_but_allows_sells():
    """Block new risk, no forced liquidation: buys skipped, exits still fill."""
    n = 12
    days = sessions(START, n)
    # Crash after entry: buy at 100, then price collapses to 60 (-40% nav move)
    closes = ["100", "100", "60", "60", "60", "60", "60", "60", "60", "60", "60", "60"]
    bars = {"AAA": make_bars("AAA", days, closes),
            "SPY": make_bars("SPY", days, ["400"] * n),
            "RSP": make_bars("RSP", days, ["150"] * n)}
    orders = [
        approval_before(days[1], 1, "AAA", OrderSide.BUY, "90"),   # ~90% deployed
        approval_before(days[5], 2, "AAA", OrderSide.BUY, "10"),   # blocked (breaker)
        approval_before(days[7], 3, "AAA", OrderSide.SELL, "90"),  # allowed (exit)
    ]
    result = simulate(make_input(bars, days, orders))
    fills = [(t.order_id, t.side) for t in result.trades]
    assert (1, OrderSide.BUY) in fills
    assert (2, OrderSide.BUY) not in fills
    assert (3, OrderSide.SELL) in fills
    blocked = [e for e in result.breaker_events
               if e.breaker == "order_gate" and "order 2" in e.reason]
    assert blocked and "new risk" in blocked[0].reason
