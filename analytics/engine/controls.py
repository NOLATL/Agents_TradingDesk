"""The four forward control benchmarks (pure), run in-simulator on IDENTICAL

timing and cost assumptions as the live book: fills go through the same
`execution.fill_price` with the same tiers, at the same next-open timing, and
marks use the same last-valid-close rule.

- spy: buy-and-hold SPY
- equal_weight_sp500_proxy: buy-and-hold RSP (proxy ETF — DECISIONS.md)
- momentum_top10: rank the configured universe by ~12-month adjusted return,
  hold top N equal-weight, rebalance on a fixed session cadence
- same_basket_no_llm_timing: the desk's basket, equal-weight, same fixed
  cadence — isolates whether LLM timing/sizing adds anything
"""

from dataclasses import dataclass, field, replace
from datetime import date
from decimal import ROUND_DOWN, Decimal

from analytics.engine.bars import bar_for, last_valid_close, trailing_adj_closes
from analytics.engine.execution import fill_price
from analytics.engine.state import (
    CENT,
    SHARE_QUANTUM,
    BarMatrix,
    BenchmarkNavRow,
    EngineConfig,
)
from schemas.enums import OrderSide

MOMENTUM_LOOKBACK_SESSIONS = 252
MOMENTUM_MIN_SESSIONS = 40


@dataclass(frozen=True)
class BenchmarkState:
    name: str
    cash: Decimal
    holdings: tuple[tuple[str, Decimal], ...] = ()   # (symbol, qty), sorted
    sessions_since_rebalance: int = 0
    initialized: bool = False


def _mark(bstate: BenchmarkState, session: date, bars: BarMatrix) -> Decimal:
    value = bstate.cash
    for symbol, qty in bstate.holdings:
        lvc = last_valid_close(bars, symbol, session)
        if lvc is not None:
            value += (qty * lvc[1]).quantize(CENT)
    return value.quantize(CENT)


def _liquidate(bstate: BenchmarkState, session: date, bars: BarMatrix,
               cfg: EngineConfig) -> BenchmarkState:
    cash = bstate.cash
    for symbol, qty in bstate.holdings:
        bar = bar_for(bars, symbol, session)
        if bar is None:
            lvc = last_valid_close(bars, symbol, session)
            if lvc is None:
                continue
            cash += (qty * lvc[1]).quantize(CENT)
            continue
        price, _ = fill_price(bar, OrderSide.SELL, None, cfg)
        cash += (qty * price).quantize(CENT)
    return replace(bstate, cash=cash.quantize(CENT), holdings=())


def _buy_equal_weight(bstate: BenchmarkState, symbols: list[str], session: date,
                      bars: BarMatrix, cfg: EngineConfig) -> BenchmarkState:
    available = [s for s in sorted(symbols) if bar_for(bars, s, session) is not None]
    if not available:
        return bstate
    per_name = bstate.cash / len(available)
    holdings: list[tuple[str, Decimal]] = []
    cash = bstate.cash
    for symbol in available:
        bar = bar_for(bars, symbol, session)
        price, _ = fill_price(bar, OrderSide.BUY, None, cfg)
        qty = (per_name / price).quantize(SHARE_QUANTUM, rounding=ROUND_DOWN)
        if qty <= 0:
            continue
        cost = (qty * price).quantize(CENT)
        cash -= cost
        holdings.append((symbol, qty))
    return replace(bstate, cash=cash.quantize(CENT), holdings=tuple(holdings),
                   sessions_since_rebalance=0, initialized=True)


def _momentum_ranking(universe: tuple[str, ...], session: date, bars: BarMatrix,
                      top_n: int) -> list[str]:
    scored: list[tuple[Decimal, str]] = []
    for symbol in sorted(universe):
        closes = trailing_adj_closes(bars, symbol, session, MOMENTUM_LOOKBACK_SESSIONS + 1)
        if len(closes) < MOMENTUM_MIN_SESSIONS or closes[0] <= 0:
            continue
        scored.append((closes[-1] / closes[0] - 1, symbol))
    scored.sort(key=lambda t: (-t[0], t[1]))  # deterministic tie-break by symbol
    return [symbol for _, symbol in scored[:top_n]]


def step_benchmark(
    bstate: BenchmarkState, session: date, bars: BarMatrix, cfg: EngineConfig
) -> tuple[BenchmarkState, BenchmarkNavRow]:
    name = bstate.name
    if name == "spy":
        targets, cadence = [cfg.spy_symbol], None
    elif name == "equal_weight_sp500_proxy":
        targets, cadence = [cfg.ewp_symbol], None
    elif name == "momentum_top10":
        targets = _momentum_ranking(cfg.momentum_universe, session, bars, cfg.momentum_top_n)
        cadence = cfg.benchmark_rebalance_sessions
    elif name == "same_basket_no_llm_timing":
        targets, cadence = list(cfg.basket), cfg.benchmark_rebalance_sessions
    else:
        raise ValueError(f"unknown benchmark {name}")

    if not bstate.initialized:
        if targets and all(bar_for(bars, s, session) for s in targets):
            bstate = _buy_equal_weight(bstate, targets, session, bars, cfg)
    elif cadence is not None:
        count = bstate.sessions_since_rebalance + 1
        if count >= cadence:
            bstate = _liquidate(bstate, session, bars, cfg)
            bstate = _buy_equal_weight(bstate, targets, session, bars, cfg)
        else:
            bstate = replace(bstate, sessions_since_rebalance=count)

    nav = _mark(bstate, session, bars)
    return bstate, BenchmarkNavRow(
        benchmark_name=name, session=session, nav=nav, contains_forced_resolution=False
    )
