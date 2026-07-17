"""Typed cross-role state (plain Pydantic — NOT LangGraph).

The EvidencePacket is the only thing roles share. Quarantined metrics travel
in a separate field that the blinding boundary strips before the analyst ever
sees context; the reviewer receives them explicitly for the
'is price driving the narrative?' check.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class Claim(BaseModel):
    """A SOURCE_FACT extracted from one cached document."""

    model_config = ConfigDict(extra="forbid")

    assertion_id: int
    document_id: int
    content_hash: str
    symbol: str
    quote: str            # verbatim span from the document (the citation)
    claim: str            # the extracted statement
    figure_name: str | None = None   # set when the claim carries a number
    figure_value: Decimal | None = None
    figure_unit: str | None = None


class ComputedMetric(BaseModel):
    """A COMPUTED assertion produced by a named analytics function."""

    model_config = ConfigDict(extra="forbid")

    assertion_id: int
    symbol: str | None
    name: str
    value: Decimal | None
    detail: dict[str, float | int | str] = Field(default_factory=dict)
    function_name: str
    code_version: str
    quarantined_from_analyst: bool = False


class MacroPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assertion_id: int
    series_id: str
    observation_date: date
    value: float | None


class EvidencePacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    theme: str
    experiment_id: int
    simulation_clock: datetime
    symbols: list[str]
    claims: list[Claim]
    metrics: list[ComputedMetric]          # non-quarantined only
    quarantined_metrics: list[ComputedMetric]  # reviewer-only (price-derived)
    macro: list[MacroPoint]


# ---- LLM output schemas (validated, then persisted via schemas/models.py) --- #

class ExtractedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quote: str = Field(description="verbatim text span copied from the document")
    claim: str = Field(description="the fact this span establishes, one sentence")
    figure_name: str | None = Field(
        default=None,
        description="snake_case name if the claim carries a number "
        "(e.g. total_revenue_fy, backlog_total, segment_revenue_electrical)",
    )
    figure_value: float | None = None
    figure_unit: str | None = Field(default=None, description="e.g. USD_millions, percent")


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ExtractedClaim]


class RankedName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    rank: int
    justification: str = Field(
        description="exposure argument citing ONLY measured metrics and claims by id"
    )
    supporting_assertion_ids: list[int]


class ForecastDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str
    probability: float = Field(ge=0.0, le=1.0)
    horizon_end: date
    resolution_criterion: str


class MilestoneDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str
    falsification_criterion: str


class AnalystOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    causal_chain: str
    basket: list[RankedName]
    valuation_check: str
    forecasts: list[ForecastDraft] = Field(min_length=6, max_length=10)
    milestones: list[MilestoneDraft] = Field(min_length=3)
    exit_criteria: dict[str, str] = Field(
        description="keys MUST be exactly PRICE_STOP, CAUSAL_BREAK, VALUATION_EXIT, TIME_STOP"
    )


class ReviewerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenges: list[str] = Field(min_length=3)
    price_narrative_check: str = Field(
        description="is price action driving the narrative? uses quarantined metrics"
    )
    do_nothing_case: str
    verdict: str = Field(description="ACTIVATE or REJECT")
    forecast_probability_revisions: dict[str, float] = Field(
        default_factory=dict,
        description="statement-index (as string) -> revised probability, only where challenged",
    )


class ReportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_summary: str = Field(
        description="3-5 sentences summarizing ONLY the provided objects; no new claims"
    )
