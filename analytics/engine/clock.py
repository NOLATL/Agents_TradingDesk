"""Session calendar helpers (XNYS via exchange_calendars). Deterministic:

calendar data is static. The engine never reads a wall clock — the simulation
clock arrives as SimulationInput.sessions; these helpers derive session
boundaries from dates only.
"""

from datetime import date, datetime, timezone

import exchange_calendars as xcals

_XNYS = xcals.get_calendar("XNYS", start="2020-01-01", end="2030-12-31")


def sessions_between(start: date, end: date) -> tuple[date, ...]:
    """All XNYS sessions in [start, end], inclusive, ascending."""
    return tuple(d.date() for d in _XNYS.sessions_in_range(str(start), str(end)))


def is_session(day: date) -> bool:
    return _XNYS.is_session(str(day))


def session_open_utc(session: date) -> datetime:
    return _XNYS.session_open(str(session)).to_pydatetime().astimezone(timezone.utc)


def session_close_utc(session: date) -> datetime:
    return _XNYS.session_close(str(session)).to_pydatetime().astimezone(timezone.utc)


def first_session_open_after(moment: datetime) -> date:
    """The first session whose open is strictly after `moment` (UTC)."""
    session = _XNYS.minute_to_future_session(moment.astimezone(timezone.utc)).date()
    # minute_to_future_session returns the next session for out-of-session
    # minutes, but for an in-session minute it returns that same session,
    # whose open is NOT after `moment` — advance one session in that case.
    if session_open_utc(session) <= moment.astimezone(timezone.utc):
        session = next_session(session)
    return session


def next_session(session: date) -> date:
    return _XNYS.next_session(str(session)).date()


def sessions_after(session: date, n: int) -> date:
    """The session n trading sessions after `session` (n >= 1)."""
    out = session
    for _ in range(n):
        out = next_session(out)
    return out
