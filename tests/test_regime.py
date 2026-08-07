"""Macro-regime analytics: percentile, banding, None-tolerant composite.
Pure functions only (analytics/regime.py)."""

from decimal import Decimal

from analytics.regime import (
    component_score_from_percentile,
    level_percentile,
    risk_on_off,
    vol_regime,
)

D = Decimal

BANDS = [("calm", D("0.25")), ("normal", D("0.75")),
         ("elevated", D("0.90")), ("stressed", D("1.00"))]


def test_level_percentile_known():
    history = [D(i) for i in range(1, 101)]        # 1..100
    assert level_percentile(D(50), history) == D("0.5")
    assert level_percentile(D(100), history) == 1
    assert level_percentile(D(0), history) == 0


def test_level_percentile_thin_history_abstains():
    assert level_percentile(D(50), [D(1)] * 59) is None


def test_vol_regime_bands():
    assert vol_regime(D("0.10"), BANDS) == "calm"
    assert vol_regime(D("0.25"), BANDS) == "calm"      # inclusive upper bound
    assert vol_regime(D("0.50"), BANDS) == "normal"
    assert vol_regime(D("0.95"), BANDS) == "stressed"
    assert vol_regime(None, BANDS) is None


def test_component_score_mapping():
    assert component_score_from_percentile(D("0.5")) == 0
    assert component_score_from_percentile(D(1)) == 2 - 1  # +1
    assert component_score_from_percentile(D(1), invert=True) == -1
    assert component_score_from_percentile(D("0.75"), invert=True) == D("-0.5")


def test_risk_on_off_weighted_and_none_tolerant():
    weights = {"vix": D("0.5"), "breadth": D("0.3"), "pct_above_avg": D("0.2")}
    score, present, total = risk_on_off(
        {"vix": D("-0.8"), "breadth": D("0.5"), "pct_above_avg": None}, weights)
    assert present == 2 and total == 3
    # renormalized: (-0.8*0.5 + 0.5*0.3) / 0.8
    assert score == (D("-0.8") * D("0.5") + D("0.5") * D("0.3")) / D("0.8")


def test_risk_on_off_all_absent():
    weights = {"vix": D("1")}
    assert risk_on_off({"vix": None}, weights) == (None, 0, 1)
