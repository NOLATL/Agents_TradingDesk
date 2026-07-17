"""Cluster risk budgeting (spec §5/§6) — NOT a covariance optimizer.

Each theme/cluster receives a fixed share of total portfolio risk; within a
cluster, weight is inverse to annualized volatility. Ledoit-Wolf-shrunk
correlations are used ONLY to identify clusters (single-linkage over a
threshold), never inverted.

FLOAT ZONE (documented, test_purity.py): this module runs at PROPOSAL time,
upstream of the replay boundary — its output becomes Order.proposed_qty which
a human approves; approved orders are the replay inputs, so numerical library
nondeterminism can never break ledger replay.

The §8 caps enforcer (`enforce_caps`) is exact-Decimal and deterministic; it
doubles as the order validator: 10% single name, 25% theme, 30% sector,
cluster risk budget, 100% deployable.
"""

from decimal import ROUND_DOWN, Decimal

import numpy as np
from sklearn.covariance import LedoitWolf

from analytics.engine.state import CENT, SHARE_QUANTUM

HUNDRED = Decimal(100)


def identify_clusters(
    returns_by_symbol: dict[str, list[float]], correlation_threshold: float
) -> dict[str, int]:
    """Symbol -> cluster id via single-linkage over Ledoit-Wolf-shrunk

    correlations. Deterministic: symbols processed sorted; cluster ids are
    dense ints in first-seen (sorted) order."""
    symbols = sorted(returns_by_symbol)
    if len(symbols) == 1:
        return {symbols[0]: 0}
    n = min(len(returns_by_symbol[s]) for s in symbols)
    matrix = np.array([returns_by_symbol[s][-n:] for s in symbols]).T
    cov = LedoitWolf().fit(matrix).covariance_
    std = np.sqrt(np.diag(cov))
    corr = cov / np.outer(std, std)

    parent = list(range(len(symbols)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            if corr[i, j] >= correlation_threshold:
                parent[find(j)] = find(i)

    roots: dict[int, int] = {}
    out: dict[str, int] = {}
    for i, symbol in enumerate(symbols):
        root = find(i)
        if root not in roots:
            roots[root] = len(roots)
        out[symbol] = roots[root]
    return out


def inverse_vol_weights(vols: dict[str, Decimal]) -> dict[str, Decimal]:
    """Within-cluster weights proportional to 1/vol (Decimal-exact)."""
    inverses = {s: (Decimal(1) / v if v > 0 else Decimal(0)) for s, v in sorted(vols.items())}
    total = sum(inverses.values())
    if total == 0:
        n = len(vols)
        return {s: Decimal(1) / n for s in vols}
    return {s: inv / total for s, inv in inverses.items()}


def size_basket(
    *,
    nav: Decimal,
    cluster_of: dict[str, int],
    vol_by_symbol: dict[str, Decimal],
    risk_share_per_cluster: Decimal,
    prices: dict[str, Decimal],
) -> dict[str, Decimal]:
    """Proposed quantities: each cluster gets `risk_share_per_cluster` of NAV

    as its capital budget (fixed share, not optimized), split inverse-vol
    within the cluster."""
    clusters: dict[int, list[str]] = {}
    for symbol in sorted(cluster_of):
        clusters.setdefault(cluster_of[symbol], []).append(symbol)

    qty: dict[str, Decimal] = {}
    for cluster_id in sorted(clusters):
        members = clusters[cluster_id]
        budget = (nav * risk_share_per_cluster).quantize(CENT)
        weights = inverse_vol_weights({s: vol_by_symbol[s] for s in members})
        for symbol in members:
            alloc = (budget * weights[symbol]).quantize(CENT)
            price = prices[symbol]
            qty[symbol] = (alloc / price).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)
    return qty


def enforce_caps(
    qty_by_symbol: dict[str, Decimal],
    *,
    prices: dict[str, Decimal],
    nav: Decimal,
    theme_of: dict[str, str],
    sector_of: dict[str, str],
    max_single_name_pct: Decimal,
    max_theme_pct: Decimal,
    max_sector_pct: Decimal,
) -> dict[str, Decimal]:
    """Hard deterministic §8 caps. Scales down (never up) until every cap

    holds; symbols processed in sorted order for determinism. Usable both at
    proposal time and as an order validator."""
    qty = dict(sorted(qty_by_symbol.items()))

    def value(s: str) -> Decimal:
        return (qty[s] * prices[s]).quantize(CENT)

    # Single-name cap: direct clip.
    single_limit = (nav * max_single_name_pct / HUNDRED).quantize(CENT)
    for s in sorted(qty):
        if value(s) > single_limit:
            qty[s] = (single_limit / prices[s]).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)

    # Group caps: proportional scale-down within the offending group.
    for group_of, limit_pct in ((theme_of, max_theme_pct), (sector_of, max_sector_pct)):
        limit = (nav * limit_pct / HUNDRED).quantize(CENT)
        groups: dict[str, list[str]] = {}
        for s in sorted(qty):
            groups.setdefault(group_of.get(s, s), []).append(s)
        for gname in sorted(groups):
            members = groups[gname]
            total = sum(value(s) for s in members)
            if total > limit and total > 0:
                scale = limit / total
                for s in members:
                    qty[s] = (qty[s] * scale).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)

    # 100% deployable: total exposure never exceeds NAV.
    total = sum(value(s) for s in sorted(qty))
    if total > nav and total > 0:
        scale = nav / total
        for s in sorted(qty):
            qty[s] = (qty[s] * scale).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)
    return qty
