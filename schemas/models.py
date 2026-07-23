"""Pydantic models for every table in spec v5 §3.

One schema module, imported everywhere. Each model's `table_name` in
TABLE_MODELS maps it to its DDL twin in ddl.sql; the round-trip tests assert
field↔column parity, enum integrity, and NOT NULL on the invariant-bearing
columns. These models are the cross-boundary objects — no business logic here.

Numeric convention: money and quantities are Decimal (SQL NUMERIC); qty allows
fractional shares by design decision deferral — the simulator (Stage 2) decides
whether to round; the schema does not constrain it.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import (
    AgentRole,
    ExitType,
    ForecastOutcome,
    IncidentClass,
    IncidentStatus,
    MilestoneStatus,
    OrderSide,
    OrderStatus,
    ProvenanceType,
    ResolutionCause,
    ThemeSource,
    ThemeStatus,
    ThesisDailyVerdict,
    ThesisStatus,
)


class DeskModel(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid")


# --------------------------------------------------------------------------- #
# §3.8 immutable research cache + §3.1 bitemporal columns
# --------------------------------------------------------------------------- #

class Document(DeskModel):
    """Immutable raw research response — invariant 4: keep what you cite.

    Raw bytes stored once on first fetch, hashed, never mutated.
    """

    id: int | None = None
    content_hash: str  # UNIQUE in SQL; extractions reference this
    raw_bytes: bytes
    extracted_text: str | None = None
    retrieval_url: str
    query_params: dict[str, Any] | None = None
    source_name: str
    license_note: str | None = None
    is_raw_immutable: bool = True  # structurally always TRUE; SQL CHECK enforces
    # §3.1 bitemporal / eligibility columns
    event_time: datetime | None = None
    publication_time: datetime | None = None
    ingestion_time: datetime
    first_eligible_decision_time: datetime  # invariant 2: NOT NULL, gate on sim clock
    source_revision_time: datetime | None = None


class Extraction(DeskModel):
    """A claim pulled from a cached document, with its citation span."""

    id: int | None = None
    document_id: int
    document_content_hash: str  # denormalized on purpose: extractions cite the hash
    claim_text: str
    citation_span: str  # locator inside the document (page/char/xpath per source)
    model_tag: str
    prompt_version: str
    created_at: datetime


# --------------------------------------------------------------------------- #
# §3.2 provenance
# --------------------------------------------------------------------------- #

class Assertion(DeskModel):
    """Every stored fact, typed — invariant 1. `type` is NOT NULL in SQL.

    Which optional fields are required depends on the type (SOURCE_FACT needs
    source_doc_id + citation_span; COMPUTED needs function_name + code_version;
    HUMAN needs human_rationale). Those conditional rules are Stage 1 service
    logic; the schema guarantees the type itself can never be absent.
    """

    id: int | None = None
    type: ProvenanceType
    value: dict[str, Any]  # JSONB; JUDGMENT values are never inputs to calculations
    source_doc_id: int | None = None
    citation_span: str | None = None
    function_name: str | None = None
    code_version: str | None = None
    model_tag: str | None = None
    prompt_version: str | None = None
    human_rationale: str | None = None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Theme intake (consolidation spec §3 — migration 004)
# --------------------------------------------------------------------------- #

class Theme(DeskModel):
    """A candidate/accepted theme in the unified intake queue.

    All three sources (agent scout, absorbed economics research, user) converge
    here; `slug` doubles as strategy_tag. The one-accept-per-day cap and the
    one-econ-candidate-per-day self-limit are enforced by partial unique
    indexes on accepted_on / proposed_on. CLOSED is terminal: an explicit
    theme reject, a rejected thesis, or a dead thesis closes the thread;
    re-entry is a new row. UNDECIDED candidates (not accepted, not rejected)
    DEFER at end of day and resurface Monday — deciding nothing never kills
    a theme, only an explicit reject does.
    """

    id: int | None = None
    slug: str
    title: str
    description: str | None = None
    source: ThemeSource
    status: ThemeStatus = ThemeStatus.CANDIDATE
    research_config: dict[str, Any]  # symbols, fred_series, rss_feeds, emphasis_terms
    source_ref: str | None = None
    backfilled: bool = False  # one-time migration rows, exempt from intake caps
    proposed_on: date
    queued_on: date  # last entered the visible queue (proposal or Monday resurface)
    proposed_at: datetime
    decided_at: datetime | None = None
    accepted_on: date | None = None
    resurface_on: date | None = None
    rolled_forward_count: int = 0
    closed_reason: str | None = None  # thesis_rejected | dead | user_closed
    created_at: datetime


class ThemeSymbol(DeskModel):
    """Point-in-time universe membership: in-universe from activation_session

    onward, so the genesis replay never restates pre-activation history.
    """

    id: int | None = None
    theme_id: int
    symbol: str
    activation_session: date
    created_at: datetime


class ThesisDailyStatus(DeskModel):
    """One row per ACTIVE thesis per session — monitoring never silently quiet.

    NO_CHANGE is a written verdict; a missing row is a monitoring gap and the
    daily report renders it loudly as one.
    """

    id: int | None = None
    thesis_id: int
    session_date: date
    verdict: ThesisDailyVerdict
    summary: str
    post_trade: bool  # open position existed at check time
    created_at: datetime


# --------------------------------------------------------------------------- #
# Theses, forecasts, milestones (§3.3, §5)
# --------------------------------------------------------------------------- #

class Thesis(DeskModel):
    """A thematic thesis per the §5 template. Versioned: review or update creates

    a new row pointing at its predecessor, so pre-/post-review calibration can
    be measured (§4).
    """

    id: int | None = None
    experiment_id: int
    title: str
    strategy_tag: str  # attribution key on every thesis/order/trade
    theme_id: int | None = None  # intake-pipeline theme (migration 004); slug = strategy_tag
    status: ThesisStatus
    version: int = 1
    parent_version_id: int | None = None
    causal_chain: str  # explicit links, markdown
    exposure_basket_approach: str  # measured-exposure justification ONLY (§7 blinding)
    valuation_check: str
    exit_criteria: dict[str, Any]  # all four ExitType keys required — SQL CHECK + tests
    do_nothing_case: str | None = None  # reviewer output; absent only pre-review
    model_tag: str
    prompt_version: str
    created_at: datetime


class Forecast(DeskModel):
    """The measurement instrument (§3.3). Resolution is decoupled from trade

    exits — only CAUSAL_BREAK or horizon_end resolves (invariant 7).
    """

    id: int | None = None
    thesis_id: int
    statement: str
    probability: float = Field(ge=0.0, le=1.0)
    horizon_end: date
    resolution_criterion: str
    resolved_at: datetime | None = None
    outcome: ForecastOutcome | None = None
    resolution_cause: ResolutionCause | None = None
    brier_score: float | None = None  # COMPUTED by metrics.py, never by a model
    created_by: AgentRole
    model_tag: str
    prompt_version: str
    created_at: datetime


class Milestone(DeskModel):
    """A falsifiable step in a thesis's causal chain. FALSIFIED ⇒ CAUSAL_BREAK."""

    id: int | None = None
    thesis_id: int
    description: str
    falsification_criterion: str
    status: MilestoneStatus
    status_changed_at: datetime | None = None
    created_at: datetime


# --------------------------------------------------------------------------- #
# Orders, trades, lots, positions
# --------------------------------------------------------------------------- #

class Order(DeskModel):
    """Every order stops at the human gate (invariant 6). The original proposal

    and any human modification are stored separately so agent, human-overlay,
    and combined performance can be distinguished.
    """

    id: int | None = None
    experiment_id: int
    thesis_id: int
    symbol: str
    side: OrderSide
    status: OrderStatus
    strategy_tag: str
    proposed_qty: Decimal
    approved_qty: Decimal | None = None  # set only on human approval; may differ
    human_rationale: str | None = None   # required in SQL when approved_qty differs? No — service rule
    exit_type: ExitType | None = None    # set when this order implements an exit
    decision_packet_doc_id: int | None = None  # evidence packet shown at approval
    proposed_at: datetime
    decided_at: datetime | None = None
    created_at: datetime


class Trade(DeskModel):
    """Immutable fill record — append-only in SQL (no UPDATE/DELETE by rule)."""

    id: int | None = None
    order_id: int
    symbol: str
    side: OrderSide
    qty: Decimal
    fill_price: Decimal
    slippage_applied: Decimal
    strategy_tag: str
    exit_type: ExitType | None = None
    executed_at: datetime
    created_at: datetime


class TaxLot(DeskModel):
    """Lot-aware, wash-sale-CANDIDATE aware. Not tax-authoritative."""

    id: int | None = None
    opening_trade_id: int
    closing_trade_id: int | None = None
    symbol: str
    qty: Decimal
    cost_basis: Decimal
    acquisition_date: date
    closed_at: datetime | None = None
    realized_pnl: Decimal | None = None
    holding_period_days: int | None = None
    wash_sale_candidate: bool = False


class Position(DeskModel):
    """Current holdings snapshot. `frozen`/`frozen_mark` implement quarantine

    freeze-and-mark (§6): NAV keeps using frozen_mark so the timeline doesn't
    distort while a corporate action awaits human resolution.
    """

    id: int | None = None
    experiment_id: int
    symbol: str
    qty: Decimal
    avg_cost: Decimal
    market_value: Decimal | None = None
    frozen: bool = False
    frozen_mark: Decimal | None = None
    as_of: datetime


# --------------------------------------------------------------------------- #
# NAV series (with the §6 taint flag)
# --------------------------------------------------------------------------- #

class DailyNav(DeskModel):
    id: int | None = None
    experiment_id: int
    nav_date: date
    nav: Decimal
    cash: Decimal
    positions_value: Decimal
    contains_forced_resolution: bool = False  # §6 quarantine taint — NOT NULL in SQL


class BenchmarkNav(DeskModel):
    """The four forward controls, run in-simulator on identical timing + costs."""

    id: int | None = None
    experiment_id: int
    benchmark_name: str  # spy | equal_weight_sp500_proxy | momentum_top10 | same_basket_no_llm_timing
    nav_date: date
    nav: Decimal
    contains_forced_resolution: bool = False


# --------------------------------------------------------------------------- #
# Learning, observability, incidents, registry, genealogy
# --------------------------------------------------------------------------- #

class Lesson(DeskModel):
    """Post-mortem output. thesis vs. trade correctness are scored separately

    (§9) — `dimension` names which axis the lesson belongs to.
    """

    id: int | None = None
    thesis_id: int
    dimension: str  # causal_accuracy | milestone_accuracy | exposure_mapping | valuation | entry_timing | sizing | exit_discipline | execution | human_override
    lesson_text: str
    strategy_tag: str
    created_at: datetime


class AgentRun(DeskModel):
    """Every LLM invocation, logged — model tag + prompt version are NOT NULL."""

    id: int | None = None
    experiment_id: int
    role: AgentRole
    model_tag: str
    prompt_version: str
    input_hash: str
    output_hash: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    success: bool | None = None
    error: str | None = None


class DataIncident(DeskModel):
    """Classified vendor discrepancies and corporate-action quarantines (§6, §8)."""

    id: int | None = None
    experiment_id: int | None = None  # set for quarantine incidents; ingestion incidents may have none
    incident_class: IncidentClass
    status: IncidentStatus
    source_name: str
    symbol: str | None = None
    description: str
    detected_at: datetime
    resolved_at: datetime | None = None
    resolution_assertion_id: int | None = None  # HUMAN-provenance adjustment entry


class Experiment(DeskModel):
    """§3.4 registry row. `registry` freezes the full experiment definition

    (universe, snapshot, prompts, model hashes, commit, config, costs, rules,
    benchmarks, window, pre-declared success criteria) as JSONB; the prose
    twin lives in docs/EXPERIMENT_REGISTRY.md.
    """

    id: int | None = None
    name: str
    registry: dict[str, Any]
    code_commit: str | None = None
    config_hash: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime


class ResearchGenealogy(DeskModel):
    """§3.5: every hypothesis generated — including rejected ones. Selection

    across many themes is multiple hypothesis testing whether or not you call
    it that; `evidence_added_after_price_observation` is NOT NULL because "we
    didn't check" is not an acceptable answer.
    """

    id: int | None = None
    experiment_id: int
    hypothesis_title: str
    thesis_id: int | None = None  # set if it became a thesis
    rejected: bool
    rejection_reason: str | None = None
    variant_count: int = 1
    evidence_added_after_price_observation: bool
    created_at: datetime


class PriceBar(DeskModel):
    """Indexed read-through cache of Tiingo bars (migration 002). Derived, never

    authoritative: `source_doc_hash` points at the immutable document; the
    rebuild-proof test guarantees TRUNCATE + rebuild is bit-identical.
    """

    id: int | None = None
    symbol: str
    session_date: date
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
    split_factor: Decimal = Decimal(1)
    div_cash: Decimal = Decimal(0)
    source_doc_hash: str


class SimulationRun(DeskModel):
    """Replay manifest + result fingerprint (Gate B evidence). Same manifest ⇒

    same result_hash, or the operational breaker trips (determinism breach).
    """

    id: int | None = None
    experiment_id: int
    manifest: dict[str, Any]
    manifest_hash: str
    result_hash: str
    code_commit: str
    created_at: datetime


class CycleLog(DeskModel):
    """Stage 3 cycle idempotency claim + crash-resume checkpoint. Outcomes

    live in domain tables; this row answers 'did this period's cycle run'."""

    id: int | None = None
    cycle_type: str      # daily | weekly | event (SQL CHECK)
    period_key: str      # session date / ISO week / trigger key; UNIQUE with type
    trigger: str
    status: str          # RUNNING | COMPLETE | FAILED (SQL CHECK)
    phase: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None


# --------------------------------------------------------------------------- #
# Table registry for round-trip tests
# --------------------------------------------------------------------------- #

TABLE_MODELS: dict[str, type[DeskModel]] = {
    "themes": Theme,
    "theme_symbols": ThemeSymbol,
    "thesis_daily_status": ThesisDailyStatus,
    "documents": Document,
    "extractions": Extraction,
    "assertions": Assertion,
    "theses": Thesis,
    "forecasts": Forecast,
    "milestones": Milestone,
    "orders": Order,
    "trades": Trade,
    "tax_lots": TaxLot,
    "positions": Position,
    "daily_nav": DailyNav,
    "benchmarks_nav": BenchmarkNav,
    "lessons": Lesson,
    "agent_runs": AgentRun,
    "data_incidents": DataIncident,
    "experiments": Experiment,
    "research_genealogy": ResearchGenealogy,
    "price_bars": PriceBar,
    "simulation_runs": SimulationRun,
    "cycle_log": CycleLog,
}
