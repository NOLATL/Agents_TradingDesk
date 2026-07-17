"""Enumerations for the trading-desk data model (spec v5 §3, §5).

These are the load-bearing vocabularies of the experiment. SQL mirrors of every
enum live in ddl.sql as Postgres ENUM types; tests/test_schema_roundtrip.py
asserts the value sets match exactly.
"""

from enum import StrEnum


class ProvenanceType(StrEnum):
    """Type of every stored assertion — invariant 1: no untyped assertions.

    JUDGMENT values are never an input to any calculation.
    """

    SOURCE_FACT = "SOURCE_FACT"  # extracted from a document; citation + timestamps required
    COMPUTED = "COMPUTED"        # produced by a named Python function; version/commit required
    JUDGMENT = "JUDGMENT"        # LLM opinion, explicitly labeled
    HUMAN = "HUMAN"              # human decision/override; rationale required


class ExitType(StrEnum):
    """The four exit types (spec §5) — invariant 7: exits are decoupled from forecasts.

    Type            | Trade effect        | Thesis effect      | Forecast effect
    ----------------|---------------------|--------------------|---------------------------
    PRICE_STOP      | close that position | thesis stays active| keep scoring
    CAUSAL_BREAK    | liquidate basket    | thesis invalidated | resolve per criterion (usually FALSE)
    VALUATION_EXIT  | close at profit     | thesis stays valid | keep scoring to horizon
    TIME_STOP       | close position      | thesis closed      | mark UNRESOLVED

    Only CAUSAL_BREAK or reaching horizon_end may resolve forecasts.
    Market noise must never enter the Brier score.
    """

    PRICE_STOP = "PRICE_STOP"
    CAUSAL_BREAK = "CAUSAL_BREAK"
    VALUATION_EXIT = "VALUATION_EXIT"
    TIME_STOP = "TIME_STOP"


class ForecastOutcome(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNRESOLVED = "UNRESOLVED"


class ResolutionCause(StrEnum):
    """Why a forecast resolved (spec §3.3). PRICE_STOP/VALUATION_EXIT are

    deliberately absent: trade exits are not resolution causes.
    """

    MILESTONE = "MILESTONE"
    TIME_STOP = "TIME_STOP"
    CAUSAL_BREAK = "CAUSAL_BREAK"
    MANUAL = "MANUAL"


class ThesisStatus(StrEnum):
    DRAFT = "DRAFT"                # analyst output, pre-review
    ACTIVE = "ACTIVE"              # reviewed, live
    INVALIDATED = "INVALIDATED"    # CAUSAL_BREAK
    CLOSED = "CLOSED"              # TIME_STOP / horizon complete
    REJECTED = "REJECTED"          # never activated (research genealogy keeps it)


class OrderStatus(StrEnum):
    """Every order passes PROPOSED → human gate (invariant 6) before any fill."""

    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"          # human approved (possibly modified — logged separately)
    REJECTED = "REJECTED"          # human declined
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class MilestoneStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FALSIFIED = "FALSIFIED"        # falsified milestone ⇒ CAUSAL_BREAK path


class AgentRole(StrEnum):
    EVIDENCE_COLLECTOR = "EVIDENCE_COLLECTOR"
    RESEARCH_ANALYST = "RESEARCH_ANALYST"
    INDEPENDENT_REVIEWER = "INDEPENDENT_REVIEWER"
    REPORT_ASSEMBLER = "REPORT_ASSEMBLER"


class IncidentClass(StrEnum):
    """Vendor discrepancies are classified, not generically quarantined (Gate A)."""

    SPLIT = "SPLIT"
    DIVIDEND = "DIVIDEND"
    BAD_PRINT = "BAD_PRINT"
    SYMBOL_MAP = "SYMBOL_MAP"
    MISSING_BAR = "MISSING_BAR"
    CALENDAR = "CALENDAR"
    REVISION = "REVISION"
    UNSUPPORTED_CORPORATE_ACTION = "UNSUPPORTED_CORPORATE_ACTION"  # triggers quarantine (§6)
    STALE_DATA = "STALE_DATA"
    INCONSISTENT = "INCONSISTENT"


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED_HUMAN = "RESOLVED_HUMAN"          # human adjustment entry posted
    FORCED_LIQUIDATION = "FORCED_LIQUIDATION"  # bounded fallback; sets NAV taint flag


# Python enum ↔ SQL enum type name (single source; used by the repository's
# insert casts and by the round-trip tests).
SQL_ENUM_NAMES: dict[type[StrEnum], str] = {
    ProvenanceType: "provenance_type",
    ExitType: "exit_type",
    ForecastOutcome: "forecast_outcome",
    ResolutionCause: "resolution_cause",
    ThesisStatus: "thesis_status",
    OrderStatus: "order_status",
    OrderSide: "order_side",
    MilestoneStatus: "milestone_status",
    AgentRole: "agent_role",
    IncidentClass: "incident_class",
    IncidentStatus: "incident_status",
}
