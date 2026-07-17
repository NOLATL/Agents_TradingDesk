"""Reconciliation pattern (Gate A: discrepancies are classified, never silently

resolved). No two slice sources critically overlap yet; this wires the pattern
so Stage 2+ reconciliation extends it rather than inventing one.
"""

from datetime import datetime, timezone
from decimal import Decimal

from data import repository as repo
from schemas.enums import IncidentClass, IncidentStatus
from schemas.models import DataIncident


def classify_price_discrepancy(
    old_close: Decimal, new_close: Decimal, split_factor_candidates: tuple[int, ...] = (2, 3, 4, 5)
) -> IncidentClass:
    """Deterministic classification of a changed historical close between two

    fetches of the same source (Stooq back-adjusts history on corporate
    actions — contracts/stooq.yaml)."""
    if old_close == 0 or new_close == 0:
        return IncidentClass.BAD_PRINT
    ratio = old_close / new_close
    for f in split_factor_candidates:
        if abs(ratio - Decimal(f)) < Decimal("0.01") or abs(ratio - 1 / Decimal(f)) < Decimal("0.001"):
            return IncidentClass.SPLIT
    if Decimal("0.90") < ratio < Decimal("1.10"):
        return IncidentClass.DIVIDEND  # small uniform back-adjustment
    return IncidentClass.BAD_PRINT


def raise_incident(
    conn,
    *,
    incident_class: IncidentClass,
    source_name: str,
    symbol: str | None,
    description: str,
) -> DataIncident:
    incident = DataIncident(
        incident_class=incident_class,
        status=IncidentStatus.OPEN,
        source_name=source_name,
        symbol=symbol,
        description=description,
        detected_at=datetime.now(timezone.utc),
    )
    repo.insert(conn, incident)
    return incident
