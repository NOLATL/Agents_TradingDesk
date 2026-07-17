"""Valuation snapshot (pure functions; COMPUTED provenance).

Distinction that matters for invariant 5 (spec §7): the *current price level*
is a permitted valuation input; barred from basket construction is *historical
price performance* (returns, momentum, 52-week context). These functions
accept a single current price and fundamentals — no price series.
"""

from decimal import Decimal


def pe_ratio(price: Decimal, eps_ttm: Decimal) -> Decimal | None:
    """Trailing P/E; None when earnings are non-positive (not a huge number)."""
    if eps_ttm <= 0:
        return None
    return price / eps_ttm


def ev(market_cap: Decimal, total_debt: Decimal, cash: Decimal) -> Decimal:
    return market_cap + total_debt - cash


def ev_ebitda(enterprise_value: Decimal, ebitda_ttm: Decimal) -> Decimal | None:
    if ebitda_ttm <= 0:
        return None
    return enterprise_value / ebitda_ttm


def percentile_rank(value: Decimal, distribution: list[Decimal]) -> Decimal:
    """Fraction of the reference distribution at or below `value` (0..1).

    Used for 'is it already priced in?' vs. a name's own history or peers."""
    if not distribution:
        raise ValueError("empty reference distribution")
    below = sum(1 for x in distribution if x <= value)
    return Decimal(below) / Decimal(len(distribution))
