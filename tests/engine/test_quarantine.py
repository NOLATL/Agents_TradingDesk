"""Gate B: quarantine protocol end to end — both branches, replay-deterministic."""

from datetime import date
from decimal import Decimal

import pytest

from analytics.engine.loop import simulate
from analytics.engine.persist import canonical_bytes
from analytics.engine.state import QuarantinePhase, ResolutionEvent
from schemas.enums import IncidentClass, OrderSide
from tests.engine.factories import (
    approval_before,
    make_bars,
    make_config,
    make_input,
    sessions,
)

D = Decimal
START = date(2026, 1, 5)
N = 16


def quarantine_scenario(resolution: ResolutionEvent | None = None):
    """AAA is bought, then its bars vanish from session 5 on (delisting-like).

    Detection trips after 3 missing sessions (session index 7); the config
    window (3 sessions in factories) times out at index 10."""
    days = sessions(START, N)
    aaa_days = days[:5]  # bars stop after index 4
    bars = {
        "AAA": make_bars("AAA", aaa_days, ["100", "101", "102", "103", "104"]),
        "SPY": make_bars("SPY", days, [str(400 + i) for i in range(N)]),
        "RSP": make_bars("RSP", days, [str(150 + i) for i in range(N)]),
    }
    orders = [approval_before(days[1], 1, "AAA", OrderSide.BUY, "10")]
    return make_input(bars, days, orders,
                      resolutions=[resolution] if resolution else [])


def test_freeze_and_mark_then_timeout_forced_liquidation_with_taint():
    result = simulate(quarantine_scenario())
    # One incident, classified, ended FORCED
    assert len(result.incidents) == 1
    inc = result.incidents[0]
    assert inc.incident_class is IncidentClass.UNSUPPORTED_CORPORATE_ACTION
    assert inc.phase is QuarantinePhase.FORCED

    # Data-integrity breaker fired while the case was open
    di_events = [e for e in result.breaker_events if e.breaker == "data_integrity"]
    assert di_events and di_events[0].tripped is True

    # Forced trade at the frozen mark (last valid close = 104), zero slippage
    forced = [t for t in result.trades if t.forced]
    assert len(forced) == 1
    assert forced[0].fill_price == D("104")
    assert forced[0].slippage_applied == D("0")

    # Taint-forward: every NAV row from the forced session onward is tainted
    forced_session = forced[0].session
    for nav in result.navs:
        assert nav.contains_forced_resolution is (nav.session >= forced_session)

    # While frozen, NAV used the frozen mark (identity held throughout — loop asserts)
    frozen_snaps = [s for s in result.position_snapshots if s.frozen]
    assert frozen_snaps and all(s.frozen_mark == D("104") for s in frozen_snaps)


def test_human_resolution_prevents_forced_liquidation():
    days = sessions(START, N)
    # Detection at index 7 (3 missing sessions after index 4); resolve at index 8.
    res = ResolutionEvent(
        assertion_id=77, symbol="AAA", effective_session=days[8],
        cash_delta=D("1040.00"),   # human posts cash-merger proceeds
        close_position=True,
    )
    result = simulate(quarantine_scenario(res))
    assert not [t for t in result.trades if t.forced]
    inc = result.incidents[0]
    assert inc.phase is QuarantinePhase.RESOLVED
    assert inc.resolution_assertion_id == 77
    for nav in result.navs:
        assert nav.contains_forced_resolution is False  # no fabricated fill
    # The human's adjustment entered cash
    assert result.navs[-1].cash > result.navs[7].cash


def test_late_resolution_rejected_as_invalid_input():
    days = sessions(START, N)
    late = ResolutionEvent(
        assertion_id=78, symbol="AAA", effective_session=days[12],  # after timeout
        cash_delta=D("0"), close_position=True,
    )
    with pytest.raises(ValueError, match="after"):
        simulate(quarantine_scenario(late))


def test_both_branches_replay_deterministically():
    a = canonical_bytes(simulate(quarantine_scenario()))
    b = canonical_bytes(simulate(quarantine_scenario()))
    assert a == b
    days = sessions(START, N)
    res = ResolutionEvent(assertion_id=77, symbol="AAA",
                          effective_session=days[8], cash_delta=D("1040.00"),
                          close_position=True)
    c = canonical_bytes(simulate(quarantine_scenario(res)))
    d = canonical_bytes(simulate(quarantine_scenario(res)))
    assert c == d
    assert a != c  # different input sets are different runs, each deterministic


def test_orders_blocked_while_data_integrity_tripped():
    """An order due while the breaker is tripped is skipped and logged."""
    days = sessions(START, N)
    aaa_days = days[:5]
    bars = {
        "AAA": make_bars("AAA", aaa_days, ["100"] * 5),
        "BBB": make_bars("BBB", days, ["50"] * N),
        "SPY": make_bars("SPY", days, ["400"] * N),
        "RSP": make_bars("RSP", days, ["150"] * N),
    }
    orders = [
        approval_before(days[1], 1, "AAA", OrderSide.BUY, "10"),
        approval_before(days[9], 2, "BBB", OrderSide.BUY, "5"),  # due while tripped
    ]
    result = simulate(make_input(bars, days, orders))
    assert not [t for t in result.trades if t.symbol == "BBB"]
    gate_events = [e for e in result.breaker_events
                   if e.breaker == "order_gate" and "order 2" in e.reason]
    assert gate_events and "halting breaker" in gate_events[0].reason
