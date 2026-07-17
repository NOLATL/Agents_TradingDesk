"""Synthetic fixture builders for engine tests — no DB, no network."""

from datetime import date, datetime, timezone
from decimal import Decimal

from analytics.engine import clock
from analytics.engine.state import (
    ApprovedOrder,
    Bar,
    EngineConfig,
    SimulationInput,
    SlippageTier,
)
from schemas.enums import OrderSide

D = Decimal


def make_config(**overrides) -> EngineConfig:
    base = dict(
        initial_cash=D("10000"),
        slippage_tiers=(SlippageTier(D("0.60"), D("35")), SlippageTier(D("0.40"), D("20"))),
        default_slippage_bps=D("10"),
        quarantine_window_sessions=3,
        max_single_name_pct=D("10"),
        max_theme_pct=D("25"),
        max_sector_pct=D("30"),
        drawdown_breaker_pct=D("15"),
        spy_symbol="SPY",
        ewp_symbol="RSP",
        momentum_universe=("AAA", "BBB", "CCC"),
        momentum_top_n=2,
        basket=("AAA", "BBB"),
        benchmark_rebalance_sessions=21,
    )
    base.update(overrides)
    return EngineConfig(**base)


def make_bars(
    symbol: str,
    sessions: list[date],
    closes: list[str],
    opens: list[str] | None = None,
    splits: dict[date, str] | None = None,
    dividends: dict[date, str] | None = None,
    volume: int = 100000,
) -> dict[date, Bar]:
    """Bars with close (and open, default = close) per session; optional

    split factors / cash dividends on specific dates."""
    out: dict[date, Bar] = {}
    for i, session in enumerate(sessions):
        close = D(closes[i])
        open_ = D(opens[i]) if opens else close
        out[session] = Bar(
            session=session,
            open=open_, high=max(open_, close), low=min(open_, close), close=close,
            volume=volume,
            adj_open=open_, adj_high=max(open_, close), adj_low=min(open_, close),
            adj_close=close, adj_volume=D(volume),
            split_factor=D((splits or {}).get(session, "1")),
            div_cash=D((dividends or {}).get(session, "0")),
        )
    return out


def sessions(start: date, n: int) -> list[date]:
    out = [start if clock.is_session(start) else clock.next_session(start)]
    while len(out) < n:
        out.append(clock.next_session(out[-1]))
    return out


def approval_before(session: date, order_id: int, symbol: str, side: OrderSide,
                    qty: str, tag: str = "test", exit_type=None, forced=False) -> ApprovedOrder:
    """An order approved the evening before `session` (so it fills at that open)."""
    prev = clock.sessions_between(date(2020, 1, 2), session)[-2]
    decided = clock.session_close_utc(prev)
    return ApprovedOrder(
        order_id=order_id, symbol=symbol, side=side, qty=Decimal(qty),
        decided_at=decided, strategy_tag=tag, exit_type=exit_type, forced=forced,
    )


def make_input(
    bars_by_symbol: dict[str, dict[date, Bar]],
    session_list: list[date],
    orders: list[ApprovedOrder] = (),
    resolutions=(),
    config: EngineConfig | None = None,
    experiment_id: int = 1,
) -> SimulationInput:
    return SimulationInput(
        experiment_id=experiment_id,
        sessions=tuple(session_list),
        bars=bars_by_symbol,
        approved_orders=tuple(sorted(orders, key=lambda o: (o.decided_at, o.order_id))),
        resolutions=tuple(resolutions),
        config=config or make_config(),
    )
