"""Engine state and result types — all frozen dataclasses, Decimal-only money.

SimulationInput is the COMPLETE replayable input set; SimulationResult is the
complete output. Nothing else flows in or out of `simulate()` (see loop.py).
Result rows carry dense sequence numbers (trade_seq, lot_seq), never DB ids —
canonical serialization of a result is therefore stable across runs and
machines, which is what makes Gate B a byte comparison.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from schemas.enums import ExitType, IncidentClass, OrderSide

CENT = Decimal("0.01")
SHARE_QUANTUM = Decimal("0.0001")


# ---------------------------- inputs ---------------------------------------- #

@dataclass(frozen=True)
class Bar:
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adj_open: Decimal
    adj_high: Decimal
    adj_low: Decimal
    adj_close: Decimal
    adj_volume: Decimal
    split_factor: Decimal
    div_cash: Decimal


BarMatrix = Mapping[str, Mapping[date, Bar]]


@dataclass(frozen=True)
class SlippageTier:
    """Applies when annualized volatility >= vol_threshold (tiers checked in

    descending threshold order; first match wins)."""

    vol_threshold: Decimal
    bps: Decimal


@dataclass(frozen=True)
class EngineConfig:
    initial_cash: Decimal
    slippage_tiers: tuple[SlippageTier, ...]      # sorted descending by threshold
    default_slippage_bps: Decimal
    quarantine_window_sessions: int
    max_single_name_pct: Decimal                  # §8 caps (of NAV)
    max_theme_pct: Decimal
    max_sector_pct: Decimal
    drawdown_breaker_pct: Decimal                 # market-risk breaker watermark
    spy_symbol: str
    ewp_symbol: str                               # equal-weight S&P proxy (RSP)
    momentum_universe: tuple[str, ...]
    momentum_top_n: int
    basket: tuple[str, ...]                       # same-basket-no-llm-timing control
    benchmark_rebalance_sessions: int             # fixed-cadence rebalance interval


@dataclass(frozen=True)
class ApprovedOrder:
    """A replay input: either human-approved (invariant 6) or the system's

    forced liquidation (§6 fallback, forced=True)."""

    order_id: int
    symbol: str
    side: OrderSide
    qty: Decimal
    decided_at: datetime
    strategy_tag: str
    exit_type: ExitType | None = None
    forced: bool = False


@dataclass(frozen=True)
class ResolutionEvent:
    """A HUMAN quarantine resolution, loaded from a HUMAN-provenance assertion.

    Replay input: with it the incident resolves; without it the timeout rule
    fires. effective_session must be <= the incident's timeout session."""

    assertion_id: int
    symbol: str
    effective_session: date
    cash_delta: Decimal            # adjustment posted by the human
    close_position: bool           # True: position removed (e.g. cash merger)


@dataclass(frozen=True)
class SimulationInput:
    experiment_id: int
    sessions: tuple[date, ...]                    # ascending
    bars: BarMatrix
    approved_orders: tuple[ApprovedOrder, ...]    # sorted (decided_at, order_id)
    resolutions: tuple[ResolutionEvent, ...]
    config: EngineConfig


# ---------------------------- portfolio state -------------------------------- #

@dataclass(frozen=True)
class OpenLot:
    lot_seq: int
    symbol: str
    qty: Decimal
    cost_basis: Decimal            # total, not per share
    acquisition_date: date
    opening_trade_seq: int


@dataclass(frozen=True)
class PositionState:
    symbol: str
    qty: Decimal
    open_lots: tuple[OpenLot, ...]
    frozen: bool = False
    frozen_mark: Decimal | None = None


class QuarantinePhase(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    FORCED = "FORCED"


@dataclass(frozen=True)
class QuarantineCase:
    symbol: str
    detected_session: date
    timeout_session: date
    incident_class: IncidentClass
    description: str
    phase: QuarantinePhase = QuarantinePhase.OPEN
    resolution_assertion_id: int | None = None
    resolved_session: date | None = None


@dataclass(frozen=True)
class BreakerState:
    market_risk_tripped: bool = False
    data_integrity_tripped: bool = False
    operational_tripped: bool = False
    model_quality_tripped: bool = False
    high_water_mark: Decimal = Decimal(0)

    @property
    def blocks_new_risk(self) -> bool:
        return self.market_risk_tripped or self.data_integrity_tripped or self.operational_tripped


@dataclass(frozen=True)
class PortfolioState:
    cash: Decimal
    positions: tuple[PositionState, ...]          # kept sorted by symbol
    quarantine: tuple[QuarantineCase, ...]
    breakers: BreakerState
    tainted: bool = False                          # taint-forward flag (§6)
    next_trade_seq: int = 1
    next_lot_seq: int = 1

    def position(self, symbol: str) -> PositionState | None:
        for p in self.positions:
            if p.symbol == symbol:
                return p
        return None


# ---------------------------- result rows ------------------------------------ #

@dataclass(frozen=True)
class TradeRow:
    trade_seq: int
    order_id: int              # 0 for forced liquidations (persist synthesizes the order)
    symbol: str
    side: OrderSide
    qty: Decimal
    fill_price: Decimal
    slippage_applied: Decimal
    strategy_tag: str
    session: date
    exit_type: ExitType | None = None
    forced: bool = False       # §6 timeout fallback — the narrow invariant-6 exception


@dataclass(frozen=True)
class LotRow:
    lot_seq: int
    opening_trade_seq: int
    symbol: str
    qty: Decimal
    cost_basis: Decimal
    acquisition_date: date
    closing_trade_seq: int | None = None
    closed_session: date | None = None
    realized_pnl: Decimal | None = None
    holding_period_days: int | None = None
    wash_sale_candidate: bool = False


@dataclass(frozen=True)
class NavRow:
    session: date
    nav: Decimal
    cash: Decimal
    positions_value: Decimal
    contains_forced_resolution: bool


@dataclass(frozen=True)
class BenchmarkNavRow:
    benchmark_name: str
    session: date
    nav: Decimal
    contains_forced_resolution: bool


@dataclass(frozen=True)
class PositionSnapshot:
    session: date
    symbol: str
    qty: Decimal
    avg_cost: Decimal
    market_value: Decimal | None
    frozen: bool
    frozen_mark: Decimal | None


@dataclass(frozen=True)
class IncidentRow:
    symbol: str
    incident_class: IncidentClass
    detected_session: date
    description: str
    phase: QuarantinePhase
    resolution_assertion_id: int | None
    resolved_session: date | None


@dataclass(frozen=True)
class BreakerEvent:
    session: date
    breaker: str
    tripped: bool
    reason: str


@dataclass(frozen=True)
class SimulationResult:
    experiment_id: int
    trades: tuple[TradeRow, ...]
    lots: tuple[LotRow, ...]
    navs: tuple[NavRow, ...]
    benchmark_navs: tuple[BenchmarkNavRow, ...]
    position_snapshots: tuple[PositionSnapshot, ...]
    incidents: tuple[IncidentRow, ...]
    breaker_events: tuple[BreakerEvent, ...]


@dataclass(frozen=True)
class ReplayManifest:
    experiment_id: int
    code_commit: str
    config_hash: str
    document_hashes: tuple[str, ...]
    approved_order_ids: tuple[int, ...]
    resolution_assertion_ids: tuple[int, ...]
    session_start: date
    session_end: date
    initial_cash: str                              # Decimal as str (JSON-safe)
    # Stage 3: HUMAN bar-pin assertions (kind='bar_pin') — vendor-restatement
    # resolutions. load_input overlays the pinned document's bars for sessions
    # <= through_session, so the ledger's history replays from the bars it
    # actually filled on. Fills are historical facts; never restated.
    bar_pin_assertion_ids: tuple[int, ...] = ()


@dataclass
class ResultAccumulator:
    trades: list = field(default_factory=list)
    lots: dict = field(default_factory=dict)       # lot_seq -> LotRow (open lots update on close)
    navs: list = field(default_factory=list)
    benchmark_navs: list = field(default_factory=list)
    position_snapshots: list = field(default_factory=list)
    incidents: list = field(default_factory=list)
    breaker_events: list = field(default_factory=list)
