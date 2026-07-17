"""Momentum metrics — computed, stored, and QUARANTINED from the analyst.

These exist for the eventual forward controls (Stage 2) and for the reviewer's
'is price driving the narrative?' check. They are NEVER an input to basket
construction (invariant 5). Assertions built from these functions must carry
value payload key `quarantined_from_analyst: true`; the blinding boundary in
agents/roles/evidence.py refuses to place them in analyst context.
"""

from decimal import Decimal

QUARANTINE_KEY = "quarantined_from_analyst"


def simple_return(first_close: Decimal, last_close: Decimal) -> Decimal:
    if first_close <= 0:
        raise ValueError("first_close must be positive")
    return last_close / first_close - 1


def momentum_12_1(closes_by_month_end: list[Decimal]) -> Decimal:
    """Classic 12-1: return from t-12 to t-1 month-end closes (skip the last

    month). Needs >= 13 month-end closes, oldest first."""
    if len(closes_by_month_end) < 13:
        raise ValueError("need at least 13 month-end closes")
    return simple_return(closes_by_month_end[-13], closes_by_month_end[-2])
