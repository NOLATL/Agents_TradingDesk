"""Macro-regime math for the momentum scan — computed, operator-only,
QUARANTINED from the analyst (invariant 5), like all scan analytics.

Inputs are Schwab tape snapshots and index history (schwab_* documents —
never price_bars, never engine surfaces). Pure functions: Decimal in,
Decimal out, no DB, no network. Feeds the scan report's "Macro tape" block
for the HUMAN only.
"""

from collections.abc import Mapping, Sequence
from decimal import Decimal

from analytics.momentum import QUARANTINE_KEY

__all__ = [
    "QUARANTINE_KEY", "level_percentile", "vol_regime",
    "component_score_from_percentile", "risk_on_off",
]


def level_percentile(latest: Decimal, history: Sequence[Decimal],
                     *, min_obs: int = 60) -> Decimal | None:
    """Fraction of historical observations at or below `latest` (0..1).
    None when history is thinner than `min_obs` (60 for the ~10y VIX
    distribution; callers with short accumulating windows, like put/call,
    pass a lower floor)."""
    if latest is None or len(history) < min_obs:
        return None
    at_or_below = sum(1 for h in history if h <= latest)
    return Decimal(at_or_below) / len(history)


def vol_regime(percentile: Decimal | None,
               bands: Sequence[tuple[str, Decimal]]) -> str | None:
    """Label the vol percentile by the first band whose upper bound covers
    it. `bands` ascending, e.g. [(calm, .25), (normal, .75), (elevated, .9),
    (stressed, 1.0)] from config."""
    if percentile is None:
        return None
    for label, upper in bands:
        if percentile <= upper:
            return label
    return bands[-1][0] if bands else None


def component_score_from_percentile(p: Decimal, *, invert: bool = False) -> Decimal:
    """Map a 0..1 percentile onto a -1..+1 score (0.5 → 0). invert=True for
    components where HIGH percentile is risk-OFF (e.g. VIX)."""
    score = (p - Decimal("0.5")) * 2
    return -score if invert else score


def risk_on_off(
    components: Mapping[str, Decimal | None],
    weights: Mapping[str, Decimal],
) -> tuple[Decimal | None, int, int]:
    """Weighted mean of -1..+1 component scores, None-tolerant: absent
    components drop out and the remaining weights renormalize. Returns
    (composite or None, components_present, components_total)."""
    total = len(weights)
    present = {k: v for k, v in components.items()
               if v is not None and k in weights}
    if not present:
        return None, 0, total
    weight_sum = sum(weights[k] for k in present)
    if weight_sum == 0:
        return None, len(present), total
    composite = sum(v * weights[k] for k, v in present.items()) / weight_sum
    return composite, len(present), total
