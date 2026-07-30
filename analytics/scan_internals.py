"""Single-name pocket internals for the momentum scan — computed,
operator-only, QUARANTINED from the analyst (invariant 5), exactly like
analytics/scan.py which this module builds on.

Inputs are Alpaca scan bars (source_name='alpaca_scan' — never price_bars,
never engine surfaces). Every function is pure: Decimal in, Decimal out,
no DB, no network. Feeds the scan report's "Pocket internals" section for
the HUMAN only; never basket construction, theme_symbols, or LLM
analyst/scout context.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from analytics.momentum import QUARANTINE_KEY
from analytics.scan import PocketScore, breadth, cross_sectional_zscores, scan_scores

__all__ = [
    "QUARANTINE_KEY", "PocketInternals", "member_composites",
    "pct_above_moving_avg", "new_extremes", "cross_sectional_dispersion",
    "pocket_internals", "index_breadth",
]


@dataclass(frozen=True)
class PocketInternals:
    pocket: str
    members_scored: int                     # members with a full composite
    members_total: int                      # members with any data
    median_member_composite: Decimal | None
    breadth_pos: int                        # positive returns at horizon[0]
    breadth_known: int
    pct_above_avg: Decimal | None           # share of members above N-session mean
    new_highs: int                          # members closing at trailing-window max
    new_lows: int
    dispersion: Decimal | None              # population std of horizon returns


def member_composites(
    closes: Mapping[str, Sequence[Decimal]], horizons: Sequence[int],
) -> dict[str, PocketScore]:
    """Single-name composites z-scored ONCE across the full cross-section
    (all ~503 members together, never per pocket — per-pocket z on a
    handful of names blows up the scale)."""
    return scan_scores(closes, horizons)


def _median(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def pct_above_moving_avg(
    closes: Mapping[str, Sequence[Decimal]], window: int,
) -> tuple[int, int]:
    """(members above their trailing `window`-session mean, members with
    enough history). Insufficient history is an absence, not a guess."""
    above = known = 0
    for series in closes.values():
        if len(series) < window:
            continue
        known += 1
        tail = series[-window:]
        if series[-1] > sum(tail) / len(tail):
            above += 1
    return above, known


def new_extremes(
    closes: Mapping[str, Sequence[Decimal]], window: int,
) -> tuple[int, int]:
    """(members closing at their trailing-window max, at the min). A member
    needs `window` closes to count; ties with the extreme count (a close AT
    the high IS the high)."""
    highs = lows = 0
    for series in closes.values():
        if len(series) < window:
            continue
        tail = series[-window:]
        last = series[-1]
        if last >= max(tail):
            highs += 1
        if last <= min(tail):
            lows += 1
    return highs, lows


def cross_sectional_dispersion(
    returns: Mapping[str, Decimal | None],
) -> Decimal | None:
    """Population std of the members' returns at one horizon — high
    dispersion = stock-picker's tape, low = macro-driven moves. None when
    fewer than 2 members have the return."""
    known = [r for r in returns.values() if r is not None]
    if len(known) < 2:
        return None
    mean = sum(known) / len(known)
    var = sum((r - mean) ** 2 for r in known) / len(known)
    return var.sqrt()


def pocket_internals(
    pocket: str,
    member_closes: Mapping[str, Sequence[Decimal]],
    member_scores: Mapping[str, PocketScore],
    *,
    dispersion_horizon_index: int,
    avg_window: int,
    extreme_window: int,
) -> PocketInternals:
    """Aggregate one pocket's members. `member_scores` come from
    member_composites (full-cross-section z); `member_closes` is the subset
    of series belonging to this pocket."""
    scores = [member_scores[s] for s in member_closes if s in member_scores]
    composites = [s.composite for s in scores if s.composite is not None]
    first_horizon = {
        s.symbol: (s.returns[0] if s.returns else None) for s in scores
    }
    dispersion_returns = {
        s.symbol: (s.returns[dispersion_horizon_index]
                   if len(s.returns) > dispersion_horizon_index else None)
        for s in scores
    }
    pos, known = breadth(first_horizon)
    above, above_known = pct_above_moving_avg(member_closes, avg_window)
    highs, lows = new_extremes(member_closes, extreme_window)
    return PocketInternals(
        pocket=pocket,
        members_scored=len(composites),
        members_total=len(member_closes),
        median_member_composite=_median(composites),
        breadth_pos=pos,
        breadth_known=known,
        pct_above_avg=(Decimal(above) / above_known if above_known else None),
        new_highs=highs,
        new_lows=lows,
        dispersion=cross_sectional_dispersion(dispersion_returns),
    )


def index_breadth(
    closes: Mapping[str, Sequence[Decimal]],
    scores: Mapping[str, PocketScore],
    *,
    dispersion_horizon_index: int,
    avg_window: int,
    extreme_window: int,
) -> PocketInternals:
    """The whole-index internals row (label 'S&P 500')."""
    return pocket_internals(
        "S&P 500", closes, scores,
        dispersion_horizon_index=dispersion_horizon_index,
        avg_window=avg_window, extreme_window=extreme_window,
    )
