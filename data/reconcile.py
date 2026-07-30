"""Reconciliation pattern (Gate A: discrepancies are classified, never silently

resolved). No two slice sources critically overlap yet; this wires the pattern
so Stage 2+ reconciliation extends it rather than inventing one.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
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


@dataclass(frozen=True)
class PriceMismatch:
    symbol: str
    session: date
    reference_close: Decimal   # Tiingo (authoritative)
    candidate_close: Decimal   # Alpaca
    rel_diff: Decimal          # abs(candidate - reference) / reference


def compare_adjusted_closes(
    reference: dict, candidate: dict, *, symbol: str,
    rel_tolerance: Decimal, min_overlap: int,
) -> list[PriceMismatch]:
    """Deterministic cross-vendor comparison of {session: adjusted_close}
    maps (three-vendor architecture Phase A, DECISIONS.md 2026-07-30).
    Below min_overlap shared sessions the comparison abstains (empty list —
    no verdict from thin evidence). The referee only reports; it never
    mutates price_bars (Tiingo stays authoritative)."""
    shared = sorted(set(reference) & set(candidate))
    if len(shared) < min_overlap:
        return []
    out = []
    for session in shared:
        ref, cand = reference[session], candidate[session]
        if ref == 0:
            continue
        rel = abs(cand - ref) / ref
        if rel > rel_tolerance:
            out.append(PriceMismatch(
                symbol=symbol, session=session, reference_close=ref,
                candidate_close=cand, rel_diff=rel))
    return out


def referee_universe(
    conn, session, *, symbols: list[str], window_sessions: int,
    rel_tolerance: Decimal, min_overlap: int,
) -> list[PriceMismatch]:
    """Compare price_bars (Tiingo adjusted, engine truth) vs the newest
    alpaca_scan adjusted closes over the trailing window for the given
    symbols (theme universe + benchmarks). Read-only; callers raise
    VENDOR_MISMATCH incidents per affected symbol."""
    from data.adapters.alpaca import latest_scan_bars

    alpaca_bars, _ = latest_scan_bars(conn, adjustment="all")
    mismatches: list[PriceMismatch] = []
    for symbol in symbols:
        rows = conn.execute(
            "SELECT session_date, adj_close FROM price_bars WHERE symbol = %s"
            " AND session_date <= %s ORDER BY session_date DESC LIMIT %s",
            (symbol, session, window_sessions),
        ).fetchall()
        if not rows or symbol not in alpaca_bars:
            continue
        reference = {r[0]: r[1] for r in rows}
        candidate = {b.session: b.close for b in alpaca_bars[symbol]
                     if b.session <= session}
        mismatches += compare_adjusted_closes(
            reference, candidate, symbol=symbol,
            rel_tolerance=rel_tolerance, min_overlap=min_overlap)
    return mismatches


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
