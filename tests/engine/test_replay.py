"""Gate B headline: bit-identical replay, plus the accounting-identity property.

Pure — no DB. The DB round-trip half lives in tests/engine/test_gate_b_db.py.
"""

from datetime import date
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from analytics.engine.loop import simulate
from analytics.engine.persist import canonical_bytes
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


def scenario_input(n_sessions=15):
    days = sessions(START, n_sessions)
    closes = [str(100 + i) for i in range(n_sessions)]
    bars = {
        "AAA": make_bars("AAA", days, closes),
        "BBB": make_bars("BBB", days, [str(50 + i * 2) for i in range(n_sessions)],
                         dividends={days[5]: "0.75"}),
        "SPY": make_bars("SPY", days, [str(400 + i) for i in range(n_sessions)]),
        "RSP": make_bars("RSP", days, [str(150 + i) for i in range(n_sessions)]),
        "CCC": make_bars("CCC", days, [str(20 + i) for i in range(n_sessions)],
                         splits={days[7]: "2"}),
    }
    orders = [
        approval_before(days[1], 1, "AAA", OrderSide.BUY, "10"),
        approval_before(days[2], 2, "BBB", OrderSide.BUY, "20"),
        approval_before(days[3], 3, "CCC", OrderSide.BUY, "50"),
        approval_before(days[9], 4, "AAA", OrderSide.SELL, "4"),
    ]
    return make_input(bars, days, orders)


def test_replay_is_bit_identical():
    """THE Gate B test: same input ⇒ byte-identical canonical result."""
    inp1, inp2 = scenario_input(), scenario_input()  # built twice, independently
    assert canonical_bytes(simulate(inp1)) == canonical_bytes(simulate(inp2))


def test_scenario_known_answers():
    result = simulate(scenario_input())
    assert len(result.trades) == 4
    # Dividend on BBB day 5: 20 shares * 0.75 credited
    # Split on CCC day 7: 50 -> 100 shares
    final_snap = {s.symbol: s for s in result.position_snapshots
                  if s.session == result.navs[-1].session}
    assert final_snap["CCC"].qty == D("100.0000")
    assert final_snap["AAA"].qty == D("6.0000")
    # Identity held every session (loop raises otherwise) and navs exist
    assert len(result.navs) == 15
    for nav in result.navs:
        assert nav.nav == nav.cash + nav.positions_value
        assert nav.contains_forced_resolution is False
    # Benchmarks ran every session on identical timing
    assert len(result.benchmark_navs) == 15 * 4


def test_fills_use_next_open_with_adverse_slippage():
    result = simulate(scenario_input())
    buy = result.trades[0]
    # AAA open on its fill day = close (factory), price 101; +10bps
    assert buy.fill_price == D("101.10") and buy.slippage_applied == D("0.10")
    sell = result.trades[3]
    assert sell.side is OrderSide.SELL
    assert sell.fill_price < D(str(100 + 9))  # sold below the open: adverse


@settings(max_examples=25, deadline=None)
@given(
    qtys=st.lists(st.integers(min_value=1, max_value=20), min_size=1, max_size=4),
    price_drift=st.integers(min_value=-30, max_value=30),
)
def test_property_identity_and_determinism(qtys, price_drift):
    n = 10
    days = sessions(START, n)
    closes = [str(max(5, 100 + price_drift * i // n)) for i in range(n)]
    bars = {"AAA": make_bars("AAA", days, closes),
            "SPY": make_bars("SPY", days, closes),
            "RSP": make_bars("RSP", days, closes)}
    orders = [
        approval_before(days[i + 1], i + 1, "AAA", OrderSide.BUY, str(q))
        for i, q in enumerate(qtys)
    ]
    inp = make_input(bars, days, orders)
    r1, r2 = simulate(inp), simulate(inp)
    assert canonical_bytes(r1) == canonical_bytes(r2)
    for nav in r1.navs:
        assert nav.nav == nav.cash + nav.positions_value
