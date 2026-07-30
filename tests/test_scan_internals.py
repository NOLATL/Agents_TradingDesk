"""Pocket-internals analytics: known-input vectors, degenerate cross-sections.
Pure functions only (analytics/scan_internals.py)."""

from decimal import Decimal

from analytics.scan_internals import (
    cross_sectional_dispersion,
    index_breadth,
    member_composites,
    new_extremes,
    pct_above_moving_avg,
    pocket_internals,
)

D = Decimal


def _series(*closes):
    return [D(str(c)) for c in closes]


def test_pct_above_moving_avg_known():
    closes = {
        "UP": _series(1, 1, 1, 2),      # last 2 > mean(1,1,1,2)=1.25
        "FLAT": _series(2, 2, 2, 2),    # 2 == mean → not above
        "SHORT": _series(1, 2),         # insufficient history → excluded
    }
    assert pct_above_moving_avg(closes, window=4) == (1, 2)


def test_new_extremes_counts_ties_as_extremes():
    closes = {
        "HI": _series(1, 2, 3),         # closes at window max
        "LO": _series(3, 2, 1),
        "MID": _series(1, 3, 2),
        "SHORT": _series(5),
    }
    assert new_extremes(closes, window=3) == (1, 1)


def test_dispersion_known_and_degenerate():
    d = cross_sectional_dispersion({"A": D("0.1"), "B": D("-0.1"), "C": None})
    assert d == D("0.1")               # population std of {0.1, -0.1}
    assert cross_sectional_dispersion({"A": D("0.1")}) is None
    assert cross_sectional_dispersion({}) is None


def test_pocket_internals_aggregates():
    closes = {
        "AAA": _series(*range(100, 171, 10)),   # 100..170, rising: new high
        "BBB": _series(*range(170, 99, -10)),   # falling: new low
    }
    scores = member_composites(closes, horizons=[1, 3])
    it = pocket_internals(
        "Test", closes, scores,
        dispersion_horizon_index=1, avg_window=4, extreme_window=4)
    assert it.members_total == 2 and it.members_scored == 2
    assert it.breadth_pos == 1 and it.breadth_known == 2
    assert it.pct_above_avg == D("0.5")
    assert (it.new_highs, it.new_lows) == (1, 1)
    assert it.median_member_composite == 0    # z-scores are symmetric for 2 members
    assert it.dispersion is not None and it.dispersion > 0


def test_pocket_internals_median_odd_and_missing_scores():
    closes = {"A": _series(1, 2), "B": _series(2, 1), "C": _series(1, 1)}
    scores = member_composites(closes, horizons=[1])
    it = pocket_internals("T", closes, scores,
                          dispersion_horizon_index=0, avg_window=2, extreme_window=2)
    assert it.members_scored == 3
    assert it.median_member_composite == scores["C"].composite  # middle value


def test_index_breadth_labels_row():
    closes = {"A": _series(1, 2), "B": _series(2, 1)}
    scores = member_composites(closes, horizons=[1])
    row = index_breadth(closes, scores,
                        dispersion_horizon_index=0, avg_window=2, extreme_window=2)
    assert row.pocket == "S&P 500"
    assert row.members_total == 2
