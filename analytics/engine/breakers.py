"""The four independent breakers (spec §8) — deterministic, EOD-only.

| Breaker        | Trips on                                   | Effect (next session)     |
|----------------|--------------------------------------------|---------------------------|
| market-risk    | drawdown from high-water mark > threshold  | block new risk (no buys)  |
| data-integrity | any OPEN quarantine case / data incident   | halt fills except forced  |
| operational    | duplicate-run / failed-reconciliation flag | halt fills except forced  |
| model-quality  | (interface only until Stage 3 wires agents)| reject proposal upstream  |

Evaluation is end-of-session only; the resulting state gates the NEXT
session's fills — no intra-session ambiguity. Reset conditions: market-risk
resets on a new high-water mark; data-integrity resets when no cases are
open; operational resets only via a logged human override (not modeled in the
lab loop — the flag is an input). LLMs cannot modify any of this (invariant 8).
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from analytics.engine.state import BreakerEvent, BreakerState, NavRow

HUNDRED = Decimal(100)


def evaluate(
    prev: BreakerState,
    session: date,
    nav_row: NavRow,
    open_quarantine_cases: int,
    drawdown_breaker_pct: Decimal,
    operational_flag: bool = False,
    model_quality_flag: bool = False,
) -> tuple[BreakerState, list[BreakerEvent]]:
    events: list[BreakerEvent] = []

    hwm = max(prev.high_water_mark, nav_row.nav)
    threshold = hwm * (HUNDRED - drawdown_breaker_pct) / HUNDRED
    market_tripped = nav_row.nav < threshold
    if hwm > prev.high_water_mark and prev.market_risk_tripped:
        market_tripped = False  # reset on new high-water mark
    elif prev.market_risk_tripped and not market_tripped:
        # still below a fresh HWM? stay tripped until a new HWM is set
        market_tripped = nav_row.nav < prev.high_water_mark

    if market_tripped != prev.market_risk_tripped:
        events.append(BreakerEvent(session, "market_risk", market_tripped,
                                   f"nav {nav_row.nav} vs hwm {hwm} (limit {threshold})"))

    data_tripped = open_quarantine_cases > 0
    if data_tripped != prev.data_integrity_tripped:
        events.append(BreakerEvent(session, "data_integrity", data_tripped,
                                   f"{open_quarantine_cases} open quarantine case(s)"))

    if operational_flag != prev.operational_tripped:
        events.append(BreakerEvent(session, "operational", operational_flag,
                                   "operational flag set" if operational_flag else "cleared"))

    if model_quality_flag != prev.model_quality_tripped:
        events.append(BreakerEvent(session, "model_quality", model_quality_flag,
                                   "model-quality flag set" if model_quality_flag else "cleared"))

    return (
        replace(
            prev,
            market_risk_tripped=market_tripped,
            data_integrity_tripped=data_tripped,
            operational_tripped=operational_flag,
            model_quality_tripped=model_quality_flag,
            high_water_mark=hwm,
        ),
        events,
    )
