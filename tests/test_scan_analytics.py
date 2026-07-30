"""Momentum-scan analytics: known-input vectors + Hypothesis properties.
Pure functions only — no DB, no network (analytics/scan.py)."""

import math
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from analytics.scan import (
    PocketScore,
    breadth,
    cross_sectional_zscores,
    horizon_return,
    rank_by_composite,
    rank_deltas,
    scan_scores,
)

D = Decimal


# --------------------------------------------------------------------------- #
# horizon_return
# --------------------------------------------------------------------------- #

def test_horizon_return_known():
    closes = [D(100), D(102), D(101), D(105), D(110)]
    assert horizon_return(closes, 1) == D("110") / D("105") - 1
    assert horizon_return(closes, 4) == D("0.10")  # 100 → 110


def test_horizon_return_insufficient_history_is_none():
    assert horizon_return([D(100), D(101)], 2) is None
    assert horizon_return([], 1) is None


def test_horizon_return_nonpositive_base_is_none():
    assert horizon_return([D(0), D(1)], 1) is None


def test_horizon_return_zero_horizon_is_none():
    assert horizon_return([D(100), D(101)], 0) is None


# --------------------------------------------------------------------------- #
# cross_sectional_zscores
# --------------------------------------------------------------------------- #

def test_zscores_known_vector():
    # values 1, 2, 3: mean 2, population std sqrt(2/3)
    z = cross_sectional_zscores({"A": D(1), "B": D(2), "C": D(3)})
    assert z["B"] == 0
    assert math.isclose(float(z["C"]), 1 / math.sqrt(2 / 3), rel_tol=1e-12)
    assert math.isclose(float(z["A"]), -float(z["C"]), rel_tol=1e-12)


def test_zscores_degenerate_cross_sections_are_zero():
    assert cross_sectional_zscores({"A": D(5)}) == {"A": D(0)}
    assert cross_sectional_zscores({}) == {}
    z = cross_sectional_zscores({"A": D(2), "B": D(2), "C": D(2)})
    assert all(v == 0 for v in z.values())


@given(st.dictionaries(
    st.text(alphabet="ABCDEFGH", min_size=1, max_size=3),
    st.decimals(min_value=-100, max_value=100, allow_nan=False, places=4),
    min_size=2, max_size=8,
))
def test_zscores_sum_to_zero_and_shift_invariant(values):
    z = cross_sectional_zscores(values)
    assert math.isclose(float(sum(z.values())), 0.0, abs_tol=1e-9)
    shifted = cross_sectional_zscores({k: v + 7 for k, v in values.items()})
    for k in values:
        assert math.isclose(float(z[k]), float(shifted[k]), abs_tol=1e-9)


# --------------------------------------------------------------------------- #
# scan_scores
# --------------------------------------------------------------------------- #

def _series(*closes):
    return [D(str(c)) for c in closes]


def test_scan_scores_composite_only_when_all_horizons_present():
    closes = {
        "UP": _series(*range(100, 111)),          # 11 closes: has 1d & 5d
        "DOWN": _series(*range(110, 99, -1)),
        "SHORT": _series(100, 101),               # 2 closes: 1d only
    }
    scores = scan_scores(closes, horizons=[1, 5])
    assert scores["UP"].composite is not None
    assert scores["DOWN"].composite is not None
    assert scores["SHORT"].composite is None      # partial → no composite
    assert scores["SHORT"].returns[1] is None
    assert scores["SHORT"].zscores[0] is not None  # still in the 1d cross-section
    assert scores["UP"].composite > scores["DOWN"].composite


def test_scan_scores_composite_is_mean_of_zscores():
    closes = {"A": _series(100, 110), "B": _series(100, 100), "C": _series(100, 90)}
    scores = scan_scores(closes, horizons=[1])
    for s in scores.values():
        assert s.composite == s.zscores[0]


# --------------------------------------------------------------------------- #
# ranking + deltas
# --------------------------------------------------------------------------- #

def _score(sym, composite):
    c = None if composite is None else D(str(composite))
    return PocketScore(symbol=sym, returns=(), zscores=(), composite=c)


def test_rank_by_composite_orders_and_drops_unscored():
    scores = {
        "A": _score("A", "0.5"), "B": _score("B", "1.5"),
        "C": _score("C", None), "D": _score("D", "0.5"),
    }
    assert rank_by_composite(scores) == ["B", "A", "D"]  # tie → ticker order


@given(st.permutations(["A", "B", "C", "D", "E"]))
def test_rank_is_invariant_to_dict_order(order):
    base = {"A": "2", "B": "1", "C": "0", "D": "-1", "E": "-2"}
    scores = {sym: _score(sym, base[sym]) for sym in order}
    assert rank_by_composite(scores) == ["A", "B", "C", "D", "E"]


def test_rank_deltas_moves_and_new_entrants():
    deltas = rank_deltas(current=["B", "A", "X"], prior=["A", "B", "C"])
    assert deltas == {"B": 1, "A": -1, "X": None}


def test_breadth_counts_known_positives():
    assert breadth({"A": D("0.1"), "B": D("-0.2"), "C": None, "D": D("0")}) == (1, 3)
