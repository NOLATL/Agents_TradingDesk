"""Point-in-time universe activation (2026-07-22 unfreeze).

The freeze is retired; what protects Gate B now is dated membership: replay
across an activation boundary is deterministic, and sessions before an
activation produce byte-identical benchmark history whether or not the
later-activated symbol exists in the config at all.
"""

from datetime import date
from decimal import Decimal

from analytics.engine.controls import _activated
from analytics.engine.loop import simulate
from analytics.engine.persist import canonical_bytes, config_hash
from tests.engine.factories import make_bars, make_config, make_input, sessions

D = Decimal
START = date(2026, 1, 5)


def _bars(days):
    return {
        "AAA": make_bars("AAA", days, [str(100 + i) for i in range(len(days))]),
        "BBB": make_bars("BBB", days, [str(50 + 2 * i) for i in range(len(days))]),
        "CCC": make_bars("CCC", days, [str(20 + 3 * i) for i in range(len(days))]),
        "SPY": make_bars("SPY", days, [str(400 + i) for i in range(len(days))]),
        "RSP": make_bars("RSP", days, [str(150 + i) for i in range(len(days))]),
    }


def test_activated_gate():
    day = date(2026, 7, 20)
    acts = (("CCC", day),)
    assert _activated((), "CCC", date(2020, 1, 2))          # no records: all active
    assert not _activated(acts, "CCC", date(2026, 7, 17))   # before activation
    assert _activated(acts, "CCC", day)                     # from activation on
    assert _activated(acts, "AAA", date(2020, 1, 2))        # unlisted: active


def test_replay_across_activation_boundary_is_bit_identical():
    days = sessions(START, 12)
    cfg = make_config(
        basket=("AAA", "BBB", "CCC"),
        basket_activations=(("CCC", days[6]),),
        universe_activations=(("CCC", days[6]),),
        benchmark_rebalance_sessions=3,
    )
    inp1 = make_input(_bars(days), days, [], config=cfg)
    inp2 = make_input(_bars(days), days, [], config=cfg)
    assert canonical_bytes(simulate(inp1)) == canonical_bytes(simulate(inp2))


def test_pre_activation_history_never_restates():
    """same_basket NAVs before CCC's activation are identical to a config in
    which CCC never existed — accepting a theme later cannot rewrite the past."""
    days = sessions(START, 12)
    with_ccc = make_config(
        basket=("AAA", "BBB", "CCC"),
        basket_activations=(("CCC", days[6]),),
        benchmark_rebalance_sessions=3,
    )
    without_ccc = make_config(basket=("AAA", "BBB"), benchmark_rebalance_sessions=3)

    def basket_navs(cfg):
        result = simulate(make_input(_bars(days), days, [], config=cfg))
        return {
            b.session: b.nav
            for b in result.benchmark_navs
            if b.benchmark_name == "same_basket_no_llm_timing"
        }

    navs_with, navs_without = basket_navs(with_ccc), basket_navs(without_ccc)
    for session in days:
        if session < days[6]:
            assert navs_with[session] == navs_without[session], session
    # ...and the activation is not a no-op: after the first post-activation
    # rebalance the two series diverge.
    assert navs_with[days[-1]] != navs_without[days[-1]]


def test_activation_records_change_config_hash():
    """Activations are part of the hashed config — a universe change is a new
    manifest, never a silent restatement of an existing one."""
    base = make_config()
    grown = make_config(universe_activations=(("CCC", date(2026, 7, 22)),))
    assert config_hash(base) != config_hash(grown)
