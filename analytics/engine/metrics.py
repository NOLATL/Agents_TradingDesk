"""Deterministic performance + calibration metrics (post-hoc; float zone —

documented in test_purity.py; metrics never feed back into the replay path).

Exit-type decoupling (invariant 7) is enforced here structurally:
`forecast_resolution_effect` is the single authority on what a trade exit may
do to forecasts, and PRICE_STOP / VALUATION_EXIT resolve NOTHING.
"""

import math
from decimal import Decimal

from schemas.enums import ExitType, ForecastOutcome, ResolutionCause

TRADING_DAYS = 252


# ----------------------------- invariant 7 ---------------------------------- #

def forecast_resolution_effect(exit_type: ExitType) -> ResolutionCause | None:
    """What forecast resolution, if any, a trade exit of this type may cause.

    Trade effect / thesis effect / forecast effect per spec §5:
      PRICE_STOP      close position | thesis active      | None (keep scoring)
      CAUSAL_BREAK    liquidate      | thesis invalidated | CAUSAL_BREAK
      VALUATION_EXIT  close at profit| thesis valid       | None (keep scoring)
      TIME_STOP       close position | thesis closed      | TIME_STOP (UNRESOLVED)
    """
    return {
        ExitType.PRICE_STOP: None,
        ExitType.CAUSAL_BREAK: ResolutionCause.CAUSAL_BREAK,
        ExitType.VALUATION_EXIT: None,
        ExitType.TIME_STOP: ResolutionCause.TIME_STOP,
    }[exit_type]


def outcome_for_time_stop() -> ForecastOutcome:
    return ForecastOutcome.UNRESOLVED


# ----------------------------- returns/risk --------------------------------- #

def daily_returns(navs: list[Decimal]) -> list[float]:
    return [float(b) / float(a) - 1 for a, b in zip(navs, navs[1:]) if a > 0]


def sharpe(navs: list[Decimal], risk_free_daily: float = 0.0) -> float | None:
    r = [x - risk_free_daily for x in daily_returns(navs)]
    if len(r) < 2:
        return None
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    if var == 0:
        return None
    return mean / math.sqrt(var) * math.sqrt(TRADING_DAYS)


def max_drawdown(navs: list[Decimal]) -> float:
    peak, worst = float("-inf"), 0.0
    for nav in navs:
        v = float(nav)
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1)
    return worst


def calmar(navs: list[Decimal]) -> float | None:
    if len(navs) < 2 or float(navs[0]) <= 0:
        return None
    dd = abs(max_drawdown(navs))
    if dd == 0:
        return None
    total = float(navs[-1]) / float(navs[0]) - 1
    years = len(navs) / TRADING_DAYS
    annualized = (1 + total) ** (1 / years) - 1 if years > 0 else total
    return annualized / dd


def hit_rate(realized_pnls: list[Decimal]) -> float | None:
    if not realized_pnls:
        return None
    return sum(1 for p in realized_pnls if p > 0) / len(realized_pnls)


def attribution_by_tag(
    realized_by_tag: dict[str, list[Decimal]]
) -> dict[str, Decimal]:
    return {tag: sum(pnls, Decimal(0)) for tag, pnls in sorted(realized_by_tag.items())}


# ----------------------------- calibration ---------------------------------- #

def brier_score(probability: float, outcome: ForecastOutcome) -> float | None:
    """None for UNRESOLVED — unresolved forecasts never enter the score."""
    if outcome is ForecastOutcome.UNRESOLVED:
        return None
    actual = 1.0 if outcome is ForecastOutcome.TRUE else 0.0
    return (probability - actual) ** 2


def mean_brier(pairs: list[tuple[float, ForecastOutcome]]) -> float | None:
    scores = [s for p, o in pairs if (s := brier_score(p, o)) is not None]
    return sum(scores) / len(scores) if scores else None


def calibration_curve(
    pairs: list[tuple[float, ForecastOutcome]], n_buckets: int = 10
) -> list[dict]:
    """Per-bucket (predicted mean, observed frequency, count); UNRESOLVED excluded."""
    resolved = [(p, o) for p, o in pairs if o is not ForecastOutcome.UNRESOLVED]
    buckets = []
    for i in range(n_buckets):
        lo, hi = i / n_buckets, (i + 1) / n_buckets
        members = [
            (p, o) for p, o in resolved
            if lo <= p < hi or (i == n_buckets - 1 and p == 1.0)
        ]
        if not members:
            continue
        buckets.append({
            "bucket": f"{lo:.1f}-{hi:.1f}",
            "predicted_mean": sum(p for p, _ in members) / len(members),
            "observed_frequency": sum(1 for _, o in members if o is ForecastOutcome.TRUE)
            / len(members),
            "count": len(members),
        })
    return buckets


def probabilistic_sharpe(navs: list[Decimal], benchmark_sharpe: float = 0.0) -> float | None:
    """PSR (Bailey & López de Prado): P(true Sharpe > benchmark)."""
    r = daily_returns(navs)
    n = len(r)
    if n < 3:
        return None
    sr = sharpe(navs)
    if sr is None:
        return None
    sr_daily = sr / math.sqrt(TRADING_DAYS)
    mean = sum(r) / n
    var = sum((x - mean) ** 2 for x in r) / (n - 1)
    if var == 0:
        return None
    std = math.sqrt(var)
    skew = sum((x - mean) ** 3 for x in r) / (n * std**3) if std else 0.0
    kurt = sum((x - mean) ** 4 for x in r) / (n * std**4) if std else 3.0
    denom = math.sqrt(max(1e-12, 1 - skew * sr_daily + (kurt - 1) / 4 * sr_daily**2))
    z = (sr_daily - benchmark_sharpe / math.sqrt(TRADING_DAYS)) * math.sqrt(n - 1) / denom
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def block_bootstrap_ci(
    returns: list[float], block: int = 5, n_boot: int = 1000, seed: int = 20260717,
    confidence: float = 0.9,
) -> tuple[float, float] | None:
    """Seeded block bootstrap CI for the mean daily return. Uses an explicit

    LCG (Numerical Recipes constants) rather than any stdlib RNG so the
    engine-wide determinism lint stays strict — same seed ⇒ same CI, on any
    platform."""
    if len(returns) < block * 2:
        return None
    lcg_state = seed & 0xFFFFFFFF

    def lcg(bound: int) -> int:
        nonlocal lcg_state
        lcg_state = (1664525 * lcg_state + 1013904223) & 0xFFFFFFFF
        return lcg_state % bound

    n_blocks = len(returns) - block + 1
    blocks = [returns[i : i + block] for i in range(n_blocks)]
    means = []
    draws = max(1, len(returns) // block)
    for _ in range(n_boot):
        sample: list[float] = []
        for _ in range(draws):
            sample.extend(blocks[lcg(n_blocks)])
        means.append(sum(sample) / len(sample))
    means.sort()
    lo_idx = int((1 - confidence) / 2 * n_boot)
    hi_idx = int((1 + confidence) / 2 * n_boot) - 1
    return means[lo_idx], means[hi_idx]
