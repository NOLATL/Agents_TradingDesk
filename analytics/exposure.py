"""Measured-exposure metrics (pure functions; COMPUTED provenance).

These are the ONLY quantities the analyst may use to rank a basket
(invariant 5). Inputs are figures already extracted from filings with
citations; outputs become COMPUTED assertions carrying this module's
function names + code version. No network, no state, no LLM.
"""

from decimal import Decimal


def revenue_share(segment_revenue: Decimal, total_revenue: Decimal) -> Decimal:
    """Fraction of total revenue attributable to the theme-relevant segment."""
    if total_revenue <= 0:
        raise ValueError("total_revenue must be positive")
    return segment_revenue / total_revenue


def segment_profit_share(segment_profit: Decimal, total_operating_profit: Decimal) -> Decimal:
    if total_operating_profit <= 0:
        raise ValueError("total_operating_profit must be positive")
    return segment_profit / total_operating_profit


def backlog_ratio(backlog: Decimal, annual_revenue: Decimal) -> Decimal:
    """Backlog expressed in years of revenue — forward-commitment depth."""
    if annual_revenue <= 0:
        raise ValueError("annual_revenue must be positive")
    return backlog / annual_revenue


def capex_intensity(capex: Decimal, revenue: Decimal) -> Decimal:
    if revenue <= 0:
        raise ValueError("revenue must be positive")
    return capex / revenue


def emphasis_count(text: str, terms: list[str]) -> dict[str, int]:
    """Case-insensitive term frequency in filing text. Deterministic: the term

    list is versioned config, the text is a cached document's extract."""
    lower = text.lower()
    return {term: lower.count(term.lower()) for term in terms}


def emphasis_total(counts: dict[str, int]) -> int:
    return sum(counts.values())


def exposure_score(
    *,
    revenue_share_value: Decimal | None,
    backlog_ratio_value: Decimal | None,
    emphasis_total_value: int,
    emphasis_norm: int = 100,
) -> Decimal:
    """Composite exposure purity in [0, ~2+]: equal-weight mean of available

    components (missing figures are excluded from the mean, not zero-filled —
    absence of disclosure is not evidence of absence of exposure). Emphasis is
    normalized by `emphasis_norm` mentions and capped at 1.
    """
    components: list[Decimal] = []
    if revenue_share_value is not None:
        components.append(revenue_share_value)
    if backlog_ratio_value is not None:
        components.append(min(backlog_ratio_value, Decimal(2)) / 2)
    components.append(min(Decimal(emphasis_total_value) / emphasis_norm, Decimal(1)))
    return sum(components) / len(components)
