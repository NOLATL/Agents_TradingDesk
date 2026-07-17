"""Analytics unit tests: known-input vectors + Hypothesis properties."""

import math
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from analytics.exposure import (
    backlog_ratio,
    emphasis_count,
    emphasis_total,
    exposure_score,
    revenue_share,
)
from analytics.momentum import momentum_12_1, simple_return
from analytics.valuation import ev, ev_ebitda, pe_ratio, percentile_rank
from analytics.volatility import annualized_volatility, daily_log_returns

D = Decimal


# --------------------------------------------------------------------------- #
# Exposure
# --------------------------------------------------------------------------- #

def test_revenue_share_known():
    assert revenue_share(D("250"), D("1000")) == D("0.25")


def test_revenue_share_rejects_nonpositive_total():
    with pytest.raises(ValueError):
        revenue_share(D("1"), D("0"))


def test_emphasis_count_case_insensitive():
    text = "Data center demand and DATA CENTER backlog; grid interconnection."
    counts = emphasis_count(text, ["data center", "grid", "transformer"])
    assert counts == {"data center": 2, "grid": 1, "transformer": 0}
    assert emphasis_total(counts) == 3


def test_exposure_score_ignores_missing_components():
    # Only emphasis available: score = capped emphasis alone, not dragged by zeros.
    score = exposure_score(
        revenue_share_value=None, backlog_ratio_value=None, emphasis_total_value=50
    )
    assert score == D("0.5")


@given(
    seg=st.decimals(min_value="0", max_value="1000", places=2),
    total=st.decimals(min_value="0.01", max_value="1000", places=2),
)
def test_revenue_share_bounds(seg, total):
    share = revenue_share(seg, total)
    assert share >= 0
    if seg <= total:
        assert share <= 1


# --------------------------------------------------------------------------- #
# Valuation
# --------------------------------------------------------------------------- #

def test_pe_and_ev_ebitda_known():
    assert pe_ratio(D("100"), D("5")) == D("20")
    assert pe_ratio(D("100"), D("-1")) is None
    assert ev(D("1000"), D("200"), D("100")) == D("1100")
    assert ev_ebitda(D("1100"), D("100")) == D("11")
    assert ev_ebitda(D("1100"), D("0")) is None


def test_percentile_rank_known():
    dist = [D(x) for x in (10, 20, 30, 40)]
    assert percentile_rank(D("30"), dist) == D("0.75")
    assert percentile_rank(D("5"), dist) == D("0")


# --------------------------------------------------------------------------- #
# Momentum (quarantined)
# --------------------------------------------------------------------------- #

def test_momentum_12_1_known():
    closes = [D(100)] * 12 + [D(120), D(150)]  # t-12 idx 1 (100) → t-1 (120)
    assert momentum_12_1(closes) == D("0.2")


def test_momentum_needs_13_points():
    with pytest.raises(ValueError):
        momentum_12_1([D(100)] * 12)


@given(st.decimals(min_value="1", max_value="10000", places=4))
def test_zero_return_property(price):
    assert simple_return(price, price) == 0


# --------------------------------------------------------------------------- #
# Volatility (quarantined)
# --------------------------------------------------------------------------- #

def test_constant_prices_zero_vol():
    assert annualized_volatility([D(100)] * 30) == 0.0


def test_log_returns_known():
    returns = daily_log_returns([D(100), D(110)])
    assert math.isclose(returns[0], math.log(1.1))


@given(
    st.lists(st.decimals(min_value="1", max_value="1000", places=4), min_size=3, max_size=50)
)
def test_volatility_nonnegative(closes):
    assert annualized_volatility(closes) >= 0
