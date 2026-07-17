"""BarMatrix helpers (pure)."""

from datetime import date
from decimal import Decimal

from analytics.engine.state import Bar, BarMatrix


def bar_for(bars: BarMatrix, symbol: str, session: date) -> Bar | None:
    return bars.get(symbol, {}).get(session)


def last_valid_close(
    bars: BarMatrix, symbol: str, before_or_on: date
) -> tuple[date, Decimal] | None:
    """Most recent session close at or before the given date. Deterministic

    scan over the symbol's known sessions."""
    symbol_bars = bars.get(symbol, {})
    candidates = [d for d in symbol_bars if d <= before_or_on]
    if not candidates:
        return None
    d = max(candidates)
    return d, symbol_bars[d].close


def trailing_adj_closes(
    bars: BarMatrix, symbol: str, until: date, n: int
) -> list[Decimal]:
    """Last n adjusted closes at or before `until`, ascending."""
    symbol_bars = bars.get(symbol, {})
    days = sorted(d for d in symbol_bars if d <= until)[-n:]
    return [symbol_bars[d].adj_close for d in days]
