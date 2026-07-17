"""Corporate-action quarantine protocol (§6) as a pure state machine.

Sequence: unsupported/unclassifiable action detected → freeze-and-mark
(position marks at last valid close; no new orders in the symbol) →
data-integrity breaker fires (breakers.py reads open cases) → classified
incident recorded → EITHER a HUMAN ResolutionEvent (a replay input) applies at
its effective session, OR the timeout session arrives and the engine
force-liquidates at frozen_mark, setting the taint-forward flag. Both branches
are deterministic functions of the input set.

Detection rules (deterministic, conservative):
- a HELD symbol with no bar for `MISSING_SESSIONS_TRIGGER` consecutive
  sessions → suspected delisting/halt → UNSUPPORTED_CORPORATE_ACTION
- a bar with split_factor <= 0 → structurally invalid → UNSUPPORTED_CORPORATE_ACTION
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from analytics.engine import clock
from analytics.engine.bars import bar_for, last_valid_close
from analytics.engine.state import (
    BarMatrix,
    IncidentRow,
    PortfolioState,
    PositionState,
    QuarantineCase,
    QuarantinePhase,
    ResolutionEvent,
)
from schemas.enums import IncidentClass

MISSING_SESSIONS_TRIGGER = 3


def _consecutive_missing(bars: BarMatrix, symbol: str, session: date, sessions: tuple[date, ...]) -> int:
    """How many consecutive sessions up to and including `session` lack a bar."""
    n = 0
    idx = sessions.index(session)
    while idx >= 0 and bar_for(bars, symbol, sessions[idx]) is None:
        n += 1
        idx -= 1
    return n


def detect(
    state: PortfolioState, session: date, bars: BarMatrix, sessions: tuple[date, ...]
) -> list[QuarantineCase]:
    """New quarantine cases for held, not-already-quarantined symbols."""
    active = {c.symbol for c in state.quarantine if c.phase is QuarantinePhase.OPEN}
    cases: list[QuarantineCase] = []
    for pos in state.positions:
        if pos.qty == 0 or pos.symbol in active or pos.frozen:
            continue
        bar = bar_for(bars, pos.symbol, session)
        reason = None
        if bar is not None and bar.split_factor <= 0:
            reason = f"invalid split_factor {bar.split_factor} on {session}"
        elif bar is None and _consecutive_missing(bars, pos.symbol, session, sessions) >= MISSING_SESSIONS_TRIGGER:
            reason = f"no bar for {MISSING_SESSIONS_TRIGGER} consecutive sessions ending {session}"
        if reason:
            cases.append(
                QuarantineCase(
                    symbol=pos.symbol,
                    detected_session=session,
                    timeout_session=session,  # placeholder; freeze() sets the real window
                    incident_class=IncidentClass.UNSUPPORTED_CORPORATE_ACTION,
                    description=reason,
                )
            )
    return cases


def freeze(
    state: PortfolioState, cases: list[QuarantineCase], session: date, bars: BarMatrix,
    window_sessions: int,
) -> PortfolioState:
    """Freeze-and-mark each new case's position at its last valid close."""
    if not cases:
        return state
    new_positions = list(state.positions)
    new_cases = list(state.quarantine)
    for case in cases:
        lvc = last_valid_close(bars, case.symbol, session)
        mark = lvc[1] if lvc else Decimal(0)
        for i, pos in enumerate(new_positions):
            if pos.symbol == case.symbol:
                new_positions[i] = replace(pos, frozen=True, frozen_mark=mark)
        new_cases.append(
            replace(case, timeout_session=clock.sessions_after(session, window_sessions))
        )
    return replace(state, positions=tuple(new_positions), quarantine=tuple(new_cases))


def resolutions_due(
    state: PortfolioState, session: date, resolutions: tuple[ResolutionEvent, ...]
) -> list[tuple[QuarantineCase, ResolutionEvent]]:
    due = []
    open_cases = {c.symbol: c for c in state.quarantine if c.phase is QuarantinePhase.OPEN}
    for res in sorted(resolutions, key=lambda r: (r.effective_session, r.assertion_id)):
        case = open_cases.get(res.symbol)
        if case and res.effective_session == session:
            if res.effective_session > case.timeout_session:
                raise ValueError(
                    f"resolution for {res.symbol} effective {res.effective_session} is after "
                    f"timeout {case.timeout_session} — invalid replay input"
                )
            due.append((case, res))
    return due


def apply_resolution(
    state: PortfolioState, case: QuarantineCase, res: ResolutionEvent, session: date
) -> PortfolioState:
    """Human adjustment: post cash delta; optionally remove the position

    (e.g. cash merger). Unfreezes. HUMAN provenance lives in the assertion."""
    new_positions = []
    for pos in state.positions:
        if pos.symbol != case.symbol:
            new_positions.append(pos)
            continue
        if res.close_position:
            continue  # position removed by the human's adjustment entry
        new_positions.append(replace(pos, frozen=False, frozen_mark=None))
    new_cases = tuple(
        replace(c, phase=QuarantinePhase.RESOLVED, resolution_assertion_id=res.assertion_id,
                resolved_session=session)
        if c.symbol == case.symbol and c.phase is QuarantinePhase.OPEN
        else c
        for c in state.quarantine
    )
    return replace(
        state,
        positions=tuple(new_positions),
        quarantine=new_cases,
        cash=state.cash + res.cash_delta,
    )


def timeouts_due(
    state: PortfolioState, session: date, resolutions: tuple[ResolutionEvent, ...] = ()
) -> list[QuarantineCase]:
    due = [
        c for c in state.quarantine
        if c.phase is QuarantinePhase.OPEN and session >= c.timeout_session
    ]
    # A resolution that targets a timing-out case but is dated AFTER the
    # timeout is an invalid replay input — reject loudly, never apply late.
    for case in due:
        for res in resolutions:
            if res.symbol == case.symbol and res.effective_session > case.timeout_session:
                raise ValueError(
                    f"resolution for {res.symbol} effective {res.effective_session} is after "
                    f"timeout {case.timeout_session} — invalid replay input"
                )
    return due


def incident_rows(state: PortfolioState) -> list[IncidentRow]:
    return [
        IncidentRow(
            symbol=c.symbol,
            incident_class=c.incident_class,
            detected_session=c.detected_session,
            description=c.description,
            phase=c.phase,
            resolution_assertion_id=c.resolution_assertion_id,
            resolved_session=c.resolved_session,
        )
        for c in state.quarantine
    ]
