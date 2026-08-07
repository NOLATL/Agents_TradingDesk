"""Market-context math (valuation snapshot, 52-week positioning, short-
interest crowding; Phase D adds options sentiment) — computed, operator/
reviewer-domain, QUARANTINED from the analyst (invariant 5).

Deliberately separate from analytics/valuation.py: that module is analyst-
PERMITTED (current price + fundamentals only). Everything here includes
price-performance context (52-week range) or market-positioning data
(short interest, options) and must never reach basket construction or
analyst/scout context. Pure functions: Decimal in, Decimal out.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from analytics.momentum import QUARANTINE_KEY
from analytics.scan import cross_sectional_zscores
from analytics.valuation import pe_ratio, percentile_rank

__all__ = [
    "QUARANTINE_KEY", "ValuationSnapshot", "range_position",
    "valuation_snapshot", "short_interest_crowding", "days_to_cover_flag",
]


@dataclass(frozen=True)
class ValuationSnapshot:
    symbol: str
    pe: Decimal | None
    pb: Decimal | None
    peg: Decimal | None
    div_yield: Decimal | None            # percent points as vendor reports
    market_cap: Decimal | None
    beta: Decimal | None
    short_int_pct_float: Decimal | None  # percent points
    short_int_days_to_cover: Decimal | None
    range_position_52wk: Decimal | None  # 0 = at low, 1 = at high
    pe_pctile_vs_peers: Decimal | None   # 0..1 within the peer set
    pe_crosscheck_note: str | None       # vendor-PE vs computed-PE mismatch


def range_position(last: Decimal | None, lo52: Decimal | None,
                   hi52: Decimal | None) -> Decimal | None:
    """(last - lo) / (hi - lo); None on missing inputs or a degenerate
    range. Clamped to [0, 1] — quotes can tick past the tracked extremes."""
    if last is None or lo52 is None or hi52 is None or hi52 <= lo52:
        return None
    pos = (last - lo52) / (hi52 - lo52)
    return min(max(pos, Decimal(0)), Decimal(1))


def valuation_snapshot(
    symbol: str,
    *,
    vendor_pe: Decimal | None,
    pb: Decimal | None,
    peg: Decimal | None,
    div_yield: Decimal | None,
    market_cap: Decimal | None,
    beta: Decimal | None,
    short_int_pct_float: Decimal | None,
    short_int_days_to_cover: Decimal | None,
    last: Decimal | None,
    lo52: Decimal | None,
    hi52: Decimal | None,
    eps_ttm: Decimal | None,
    peer_pes: Sequence[Decimal],
    crosscheck_tolerance: Decimal = Decimal("0.05"),
) -> ValuationSnapshot:
    """Assemble one symbol's snapshot from vendor DIRECT EXTRACTS + our
    PYTHON CALCS. The vendor P/E is cross-checked against price/EPS
    (analytics.valuation.pe_ratio); a relative mismatch beyond tolerance
    becomes a note, never a silent correction (referee spirit)."""
    note = None
    if vendor_pe is not None and last is not None and eps_ttm is not None:
        computed = pe_ratio(last, eps_ttm)
        if computed is not None and vendor_pe > 0:
            rel = abs(computed - vendor_pe) / vendor_pe
            if rel > crosscheck_tolerance:
                note = (f"vendor PE {vendor_pe} vs computed {computed:.1f}"
                        f" ({float(rel) * 100:.0f}% apart)")
    pctile = None
    if vendor_pe is not None and peer_pes:
        pctile = percentile_rank(vendor_pe, list(peer_pes))
    return ValuationSnapshot(
        symbol=symbol, pe=vendor_pe, pb=pb, peg=peg, div_yield=div_yield,
        market_cap=market_cap, beta=beta,
        short_int_pct_float=short_int_pct_float,
        short_int_days_to_cover=short_int_days_to_cover,
        range_position_52wk=range_position(last, lo52, hi52),
        pe_pctile_vs_peers=pctile,
        pe_crosscheck_note=note,
    )


def short_interest_crowding(
    si_pct_float: Mapping[str, Decimal],
    members_by_pocket: Mapping[str, Sequence[str]],
) -> dict[str, Decimal]:
    """Per pocket: median member short-interest (%float), z-scored across
    pockets — which pockets are unusually crowded on the short side.
    Pockets without any member SI drop out."""
    medians: dict[str, Decimal] = {}
    for pocket, members in members_by_pocket.items():
        vals = sorted(si_pct_float[m] for m in members if m in si_pct_float)
        if not vals:
            continue
        mid = len(vals) // 2
        medians[pocket] = (vals[mid] if len(vals) % 2
                           else (vals[mid - 1] + vals[mid]) / 2)
    return cross_sectional_zscores(medians)


def days_to_cover_flag(days_to_cover: Decimal | None, threshold: Decimal) -> bool:
    """True when a squeeze-relevant days-to-cover breaches the config
    threshold."""
    return days_to_cover is not None and days_to_cover >= threshold


# --------------------------------------------------------------------------- #
# Options sentiment (Phase D) — inputs are schwab_chains DIRECT EXTRACTS
# (per-contract IV/OI/volume); everything below is PYTHON CALC.
# --------------------------------------------------------------------------- #

def put_call_ratio(contracts, *, field: str) -> Decimal | None:
    """PUT/CALL ratio over `field` ('volume' or 'open_interest'). None when
    the call side is zero (no ratio from an empty denominator)."""
    puts = sum(getattr(c, field) for c in contracts if c.put_call == "PUT")
    calls = sum(getattr(c, field) for c in contracts if c.put_call == "CALL")
    if calls <= 0:
        return None
    return Decimal(puts) / Decimal(calls)


def _atm_iv_at(contracts, spot: Decimal, dtes: Sequence[int]) -> Decimal | None:
    """Mean IV of the put+call pair at the strike nearest spot for the
    given expiry bucket."""
    pool = [c for c in contracts if c.dte in dtes and c.iv is not None]
    if not pool or spot is None:
        return None
    nearest = min(pool, key=lambda c: abs(c.strike - spot))
    at_strike = [c.iv for c in pool if c.strike == nearest.strike]
    return sum(at_strike) / len(at_strike) if at_strike else None


def atm_iv(chain, *, expiry_index: int = 0) -> Decimal | None:
    """ATM IV at the chain's expiry_index-th nearest expiry (0 = front)."""
    dtes = sorted({c.dte for c in chain.contracts if c.dte >= 0})
    if len(dtes) <= expiry_index:
        return None
    return _atm_iv_at(chain.contracts, chain.spot, [dtes[expiry_index]])


def iv_term_slope(chain, *, near_dte: int, far_dte: int) -> Decimal | None:
    """Far-minus-near ATM IV (percent points): positive = contango (calm),
    negative = inverted (near-term fear). Expiries snap to the closest
    available DTE on each side; None when the chain can't cover both."""
    dtes = sorted({c.dte for c in chain.contracts if c.dte >= 0})
    if len(dtes) < 2:
        return None
    near = min(dtes, key=lambda d: abs(d - near_dte))
    far = min(dtes, key=lambda d: abs(d - far_dte))
    if near == far:
        return None
    near_iv = _atm_iv_at(chain.contracts, chain.spot, [near])
    far_iv = _atm_iv_at(chain.contracts, chain.spot, [far])
    if near_iv is None or far_iv is None:
        return None
    return far_iv - near_iv
