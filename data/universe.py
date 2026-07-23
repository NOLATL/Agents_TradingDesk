"""Point-in-time universe derived from the themes tables (migration 004).

The universe-freeze clause is retired (DECISIONS.md 2026-07-22): accepted
themes widen the tradeable/benchmark universe under the live experiment. What
keeps the genesis replay deterministic is that membership is dated, not
frozen — a symbol is in-universe from its activation_session onward, so
sessions before an activation replay exactly as they always did.

Two dated sets, with distinct semantics:
- universe activations (theme_symbols.activation_session): membership in the
  momentum-control universe, ingestion set, and completeness gate. The genesis
  backfill activates all 15 original symbols at genesis — matching the frozen
  momentum_universe the benchmarks actually ran on — deliberately decoupled
  from theme acceptance status.
- basket activations (max of theme.accepted_on and activation_session): the
  same_basket_no_llm_timing control holds only symbols of ACCEPTED themes,
  point-in-time. Symbols never leave the basket when a thesis later dies —
  a deactivation would restate the control's history; the control measures
  timing/sizing, not exit skill.
"""

from dataclasses import replace
from datetime import date

from analytics.engine.persist import engine_config_from_yaml
from analytics.engine.state import EngineConfig


def activation_records(conn) -> tuple[tuple[str, date], ...]:
    """(symbol, first activation session), sorted by symbol — hashed into
    EngineConfig, so the manifest pins exactly which universe replayed."""
    rows = conn.execute(
        "SELECT symbol, min(activation_session) FROM theme_symbols"
        " GROUP BY symbol ORDER BY symbol"
    ).fetchall()
    return tuple((r[0], r[1]) for r in rows)


def basket_records(conn) -> tuple[tuple[str, date], ...]:
    """(symbol, session it entered the same-basket control), sorted by symbol.

    Entry session = the later of the theme's acceptance date and the symbol's
    activation session. Only themes that have been accepted contribute.
    """
    rows = conn.execute(
        "SELECT ts.symbol, min(GREATEST(t.accepted_on, ts.activation_session))"
        " FROM theme_symbols ts JOIN themes t ON ts.theme_id = t.id"
        " WHERE t.accepted_on IS NOT NULL GROUP BY ts.symbol ORDER BY ts.symbol"
    ).fetchall()
    return tuple((r[0], r[1]) for r in rows)


def universe_as_of(conn, session: date) -> list[str]:
    """Symbols in-universe for `session` (benchmark ETFs not included)."""
    return [s for s, activated in activation_records(conn) if activated <= session]


def engine_config_from_db(conn) -> EngineConfig:
    """The production EngineConfig: YAML base + DB-derived point-in-time
    universe. The YAML momentum_universe key remains as genesis documentation
    only — the DB is authoritative from the 2026-07-22 cutover.
    """
    base = engine_config_from_yaml()
    activations = activation_records(conn)
    baskets = basket_records(conn)
    return replace(
        base,
        momentum_universe=tuple(s for s, _ in activations),
        basket=tuple(s for s, _ in baskets),
        universe_activations=activations,
        basket_activations=baskets,
    )
