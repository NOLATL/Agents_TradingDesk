"""Volatility metrics — computed, stored, QUARANTINED from the analyst

(same rule as analytics/momentum.py; used later for inverse-vol sizing and
the reviewer's price-narrative check)."""

import math
from decimal import Decimal


def daily_log_returns(closes: list[Decimal]) -> list[float]:
    if len(closes) < 2:
        raise ValueError("need at least 2 closes")
    if any(c <= 0 for c in closes):
        raise ValueError("closes must be positive")
    return [math.log(float(b) / float(a)) for a, b in zip(closes, closes[1:])]


def annualized_volatility(closes: list[Decimal], trading_days: int = 252) -> float:
    """Sample stdev of daily log returns, annualized."""
    returns = daily_log_returns(closes)
    if len(returns) < 2:
        raise ValueError("need at least 3 closes for a sample stdev")
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(trading_days)
