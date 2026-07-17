"""Sizing (cluster risk budgeting, caps-bind) and metrics (incl. the

exit-type decoupling authority) tests."""

import math
from decimal import Decimal

from analytics.engine.metrics import (
    brier_score,
    calibration_curve,
    block_bootstrap_ci,
    forecast_resolution_effect,
    hit_rate,
    max_drawdown,
    mean_brier,
    sharpe,
)
from analytics.engine.sizing import (
    enforce_caps,
    identify_clusters,
    inverse_vol_weights,
    size_basket,
)
from schemas.enums import ExitType, ForecastOutcome, ResolutionCause

D = Decimal


# ----------------------------- sizing --------------------------------------- #

def correlated_series(seed_series: list[float], noise: float, k: int) -> list[float]:
    return [x + noise * ((i * 7 + k * 13) % 5 - 2) / 100 for i, x in enumerate(seed_series)]


def test_cluster_identification_groups_correlated_names():
    base = [math.sin(i / 3) / 50 + (0.001 if i % 2 else -0.001) for i in range(120)]
    other = [math.cos(i / 5) / 40 for i in range(120)]
    returns = {
        "AAA": correlated_series(base, 0.02, 1),
        "BBB": correlated_series(base, 0.02, 2),   # same theme as AAA
        "ZZZ": correlated_series(other, 0.02, 3),  # different driver
    }
    clusters = identify_clusters(returns, correlation_threshold=0.6)
    assert clusters["AAA"] == clusters["BBB"]
    assert clusters["ZZZ"] != clusters["AAA"]


def test_inverse_vol_weights():
    w = inverse_vol_weights({"A": D("0.20"), "B": D("0.40")})
    assert abs(w["A"] - 2 * w["B"]) < D("1e-20")
    assert abs(sum(w.values()) - 1) < D("1e-20")


def test_size_basket_fixed_cluster_share():
    qty = size_basket(
        nav=D("10000"),
        cluster_of={"A": 0, "B": 0, "Z": 1},
        vol_by_symbol={"A": D("0.2"), "B": D("0.2"), "Z": D("0.3")},
        risk_share_per_cluster=D("0.25"),
        prices={"A": D("100"), "B": D("100"), "Z": D("50")},
    )
    # Cluster 0 budget 2500 split evenly -> 12.5 shares each at 100
    assert qty["A"] == D("12.5000") and qty["B"] == D("12.5000")
    # Cluster 1 budget 2500 alone -> 50 shares at 50
    assert qty["Z"] == D("50.0000")


def test_caps_bind_on_correlated_basket():
    """Gate B: drive a 3-name single-theme basket into the theme cap and show

    it binds (25% of NAV) along with the 10% single-name cap."""
    nav = D("10000")
    qty = {"A": D("20"), "B": D("20"), "C": D("20")}  # 2000 each = 60% in one theme
    capped = enforce_caps(
        qty,
        prices={"A": D("100"), "B": D("100"), "C": D("100")},
        nav=nav,
        theme_of={"A": "grid", "B": "grid", "C": "grid"},
        sector_of={"A": "ind", "B": "ind", "C": "ind"},
        max_single_name_pct=D("10"),
        max_theme_pct=D("25"),
        max_sector_pct=D("30"),
    )
    values = {s: capped[s] * D("100") for s in capped}
    # Single-name cap clipped each to <= 1000; theme cap then binds at 2500 total
    assert all(v <= D("1000") for v in values.values())
    assert sum(values.values()) <= nav * D("0.25")
    assert sum(values.values()) >= nav * D("0.24")  # binds, not annihilates


def test_caps_never_scale_up():
    qty = {"A": D("1")}
    capped = enforce_caps(
        qty, prices={"A": D("100")}, nav=D("100000"),
        theme_of={"A": "t"}, sector_of={"A": "s"},
        max_single_name_pct=D("10"), max_theme_pct=D("25"), max_sector_pct=D("30"),
    )
    assert capped["A"] == D("1")


# ----------------------------- metrics -------------------------------------- #

def test_exit_type_decoupling_authority():
    """Invariant 7: the single source of truth for what an exit may resolve."""
    assert forecast_resolution_effect(ExitType.PRICE_STOP) is None
    assert forecast_resolution_effect(ExitType.VALUATION_EXIT) is None
    assert forecast_resolution_effect(ExitType.CAUSAL_BREAK) is ResolutionCause.CAUSAL_BREAK
    assert forecast_resolution_effect(ExitType.TIME_STOP) is ResolutionCause.TIME_STOP


def test_brier_and_unresolved_exclusion():
    assert brier_score(0.7, ForecastOutcome.TRUE) == (0.7 - 1) ** 2
    assert brier_score(0.7, ForecastOutcome.UNRESOLVED) is None
    pairs = [(0.7, ForecastOutcome.TRUE), (0.7, ForecastOutcome.UNRESOLVED)]
    assert mean_brier(pairs) == (0.7 - 1) ** 2  # UNRESOLVED never enters


def test_calibration_curve_buckets():
    pairs = [(0.65, ForecastOutcome.TRUE), (0.68, ForecastOutcome.FALSE),
             (0.62, ForecastOutcome.TRUE), (0.95, ForecastOutcome.TRUE)]
    curve = calibration_curve(pairs)
    b6 = next(b for b in curve if b["bucket"] == "0.6-0.7")
    assert b6["count"] == 3 and abs(b6["observed_frequency"] - 2 / 3) < 1e-9


def test_sharpe_drawdown_hit_rate():
    navs = [D(x) for x in (100, 101, 102, 101, 103, 104)]
    assert sharpe(navs) is not None and sharpe(navs) > 0
    assert max_drawdown(navs) < 0
    assert hit_rate([D("5"), D("-3"), D("2")]) == 2 / 3


def test_bootstrap_ci_deterministic():
    returns = [((i * 11) % 7 - 3) / 1000 for i in range(60)]
    assert block_bootstrap_ci(returns) == block_bootstrap_ci(returns)
