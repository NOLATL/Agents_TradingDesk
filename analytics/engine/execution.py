"""Fill model (pure). Spec §6: buy at open + adverse slippage; sell at

open − adverse slippage; RAW prices; fills at the NEXT valid session open
after approval, never same-bar. Tier selection is deterministic from the
config table (annualized volatility bands; first matching descending
threshold wins; default otherwise).
"""

from datetime import date, datetime
from decimal import Decimal

from analytics.engine import clock
from analytics.engine.state import (
    CENT,
    ApprovedOrder,
    Bar,
    EngineConfig,
    SlippageTier,
)
from schemas.enums import OrderSide

BPS = Decimal(10000)


def slippage_bps(
    annualized_vol: Decimal | None, tiers: tuple[SlippageTier, ...], default_bps: Decimal
) -> Decimal:
    if annualized_vol is not None:
        for tier in tiers:  # descending thresholds; first match wins
            if annualized_vol >= tier.vol_threshold:
                return tier.bps
    return default_bps


def fill_price(
    bar: Bar, side: OrderSide, annualized_vol: Decimal | None, cfg: EngineConfig
) -> tuple[Decimal, Decimal]:
    """(price, slippage_applied) — both quantized to cents. Adverse in BOTH

    directions: buys pay up, sells receive less."""
    bps = slippage_bps(annualized_vol, cfg.slippage_tiers, cfg.default_slippage_bps)
    slip = (bar.open * bps / BPS).quantize(CENT)
    if side is OrderSide.BUY:
        return (bar.open + slip).quantize(CENT), slip
    return (bar.open - slip).quantize(CENT), slip


def due_session(order: ApprovedOrder) -> date:
    """First session whose open is strictly after the approval moment."""
    return clock.first_session_open_after(order.decided_at)


def orders_due(
    approved: tuple[ApprovedOrder, ...], session: date
) -> list[ApprovedOrder]:
    """Orders filling this session, in deterministic (decided_at, order_id)

    order. An order whose due session has no bar for its symbol rolls forward
    (handled by the loop; this function is strict equality)."""
    due = [o for o in approved if due_session(o) == session]
    return sorted(due, key=lambda o: (o.decided_at, o.order_id))
