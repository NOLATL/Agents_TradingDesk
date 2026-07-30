"""Momentum-scan math — computed, operator-only, and QUARANTINED from the
analyst (invariant 5), exactly like analytics/momentum.py.

Feeds ops/momentum_scan.py's daily pocket report for the HUMAN to form
hypotheses from. Never an input to basket construction, theme_symbols, the
engine, or any LLM analyst/scout context; the scan narrator (a Report
Assembler call) sees only these computed values and is contained by the
narrative guard. Assertions built from these functions must carry value
payload key `quarantined_from_analyst: true`.

Pure functions: Decimal in, Decimal out, no DB, no network.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from analytics.momentum import QUARANTINE_KEY, simple_return

__all__ = [
    "QUARANTINE_KEY", "PocketScore", "horizon_return",
    "cross_sectional_zscores", "scan_scores", "rank_by_composite",
    "rank_deltas", "breadth",
]


@dataclass(frozen=True)
class PocketScore:
    symbol: str
    returns: tuple[Decimal | None, ...]   # one per horizon, oldest-shortest first
    zscores: tuple[Decimal | None, ...]   # cross-sectional, same order
    composite: Decimal | None             # mean of zscores; None unless all present


def horizon_return(adj_closes: Sequence[Decimal], horizon_sessions: int) -> Decimal | None:
    """Return over the last `horizon_sessions` sessions from an ascending
    adjusted-close series. Needs horizon+1 closes; insufficient history or a
    non-positive base close is an absence (None), never an estimate."""
    if horizon_sessions < 1 or len(adj_closes) < horizon_sessions + 1:
        return None
    base = adj_closes[-1 - horizon_sessions]
    if base <= 0:
        return None
    return simple_return(base, adj_closes[-1])


def cross_sectional_zscores(values: Mapping[str, Decimal]) -> dict[str, Decimal]:
    """Population z-scores across the given cross-section. A degenerate
    cross-section (fewer than 2 members, or zero variance) z-scores to all
    zeros rather than dividing by zero."""
    if len(values) < 2:
        return {k: Decimal(0) for k in values}
    mean = sum(values.values()) / len(values)
    var = sum((v - mean) ** 2 for v in values.values()) / len(values)
    if var == 0:
        return {k: Decimal(0) for k in values}
    std = var.sqrt()
    return {k: (v - mean) / std for k, v in values.items()}


def scan_scores(
    closes: Mapping[str, Sequence[Decimal]],
    horizons: Sequence[int],
) -> dict[str, PocketScore]:
    """Multi-horizon momentum composite for one cross-section.

    Per symbol: return over each horizon; each horizon z-scored across the
    symbols that have it; composite = mean of the z-scores, only when every
    horizon is present (a partial composite would silently mix scales)."""
    returns: dict[str, list[Decimal | None]] = {
        sym: [horizon_return(series, h) for h in horizons]
        for sym, series in closes.items()
    }
    zscores: dict[str, list[Decimal | None]] = {sym: [None] * len(horizons) for sym in returns}
    for i in range(len(horizons)):
        cross = {sym: r[i] for sym, r in returns.items() if r[i] is not None}
        for sym, z in cross_sectional_zscores(cross).items():
            zscores[sym][i] = z
    out: dict[str, PocketScore] = {}
    for sym in returns:
        zs = zscores[sym]
        composite = (
            sum(zs) / len(zs) if zs and all(z is not None for z in zs) else None
        )
        out[sym] = PocketScore(
            symbol=sym,
            returns=tuple(returns[sym]),
            zscores=tuple(zs),
            composite=composite,
        )
    return out


def rank_by_composite(scores: Mapping[str, PocketScore]) -> list[str]:
    """Symbols with a composite, best first; ticker breaks ties so the
    ranking is deterministic regardless of dict order."""
    ranked = [s for s in scores.values() if s.composite is not None]
    return [s.symbol for s in sorted(ranked, key=lambda s: (-s.composite, s.symbol))]


def rank_deltas(current: Sequence[str], prior: Sequence[str]) -> dict[str, int | None]:
    """Per current symbol: positions climbed since the prior ranking
    (positive = up); None = not ranked in the prior scan (new entrant)."""
    prior_pos = {sym: i for i, sym in enumerate(prior)}
    return {
        sym: (prior_pos[sym] - i) if sym in prior_pos else None
        for i, sym in enumerate(current)
    }


def breadth(returns: Mapping[str, Decimal | None]) -> tuple[int, int]:
    """(members with a positive return, members with a return at all)."""
    known = [r for r in returns.values() if r is not None]
    return sum(1 for r in known if r > 0), len(known)
