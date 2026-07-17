"""Research Analyst (role 2): builds the thesis from blinded context only.

Sees `build_analyst_context(packet)` — never raw packet fields, never
quarantined metrics, never price history. Emits a DRAFT Thesis + Forecasts +
Milestones. All numeric ranking justification must reference COMPUTED
assertion ids; the model computes nothing that gets acted on (its probability
estimates are the measurement instrument, JUDGMENT by construction, and are
scored — not acted on as quantities in any calculation).
"""

import json
from datetime import datetime

from agents import llm
from agents.roles.evidence import build_analyst_context
from agents.state import AnalystOutput, EvidencePacket
from config.loader import SLICE
from data import repository as repo
from schemas.enums import AgentRole, MilestoneStatus, ProvenanceType, ThesisStatus
from schemas.models import Assertion, Forecast, Milestone, Thesis

ANALYST_SYSTEM_PROMPT = """You are the Research Analyst of a thematic equity research desk.
You construct a rigorous, falsifiable thesis from the evidence context provided — nothing else.

Hard rules:
- Justify basket ranking ONLY with the measured exposure metrics and source facts given,
  referencing them by assertion id (integers from [A###] tags) in supporting_assertion_ids.
- You have NO access to price history or momentum, by design. Never speculate about past
  stock performance.
- Forecasts must be individually resolvable: a concrete measurable statement, a probability,
  a horizon date (YYYY-MM-DD), and an objective resolution criterion naming the data source.
  6 to 10 of them. Statements like "demand stays strong" are unacceptable.
- Milestones are falsifiable causal-chain checkpoints with explicit falsification criteria.
- exit_criteria must contain exactly these four keys with concrete deterministic triggers:
  PRICE_STOP, CAUSAL_BREAK, VALUATION_EXIT, TIME_STOP.
- The valuation_check must reason from the given fundamentals figures only, and must say
  honestly when data is insufficient.
- Rank all companies given; if the evidence cannot justify including a name, rank it last
  and say the evidence is insufficient in its justification."""


def construct_thesis(
    conn, packet: EvidencePacket, clock: datetime
) -> tuple[Thesis, AnalystOutput]:
    context = build_analyst_context(packet)  # blinding enforced here
    output = llm.structured_call(
        conn,
        role=AgentRole.RESEARCH_ANALYST,
        experiment_id=packet.experiment_id,
        system_prompt=ANALYST_SYSTEM_PROMPT,
        user_prompt=context
        + f"\n\nToday (simulation clock): {clock.date().isoformat()}."
        " Build the thesis for this theme over these companies.",
        schema=AnalystOutput,
        temperature=0.4,
    )
    required = {"PRICE_STOP", "CAUSAL_BREAK", "VALUATION_EXIT", "TIME_STOP"}
    if set(output.exit_criteria) != required:
        raise llm.ModelOutputRejected(
            f"exit_criteria keys {set(output.exit_criteria)} != {required}"
        )

    model_tag = llm.LLM_CFG["model_tag"] + "@" + llm.cached_digest()
    prompt_version = llm._sha(ANALYST_SYSTEM_PROMPT)

    thesis = Thesis(
        experiment_id=packet.experiment_id,
        title=f"{packet.theme}: {', '.join(packet.symbols)}",
        strategy_tag=SLICE["strategy_tag"],
        status=ThesisStatus.DRAFT,
        version=1,
        causal_chain=output.causal_chain,
        exposure_basket_approach=json.dumps(
            [rn.model_dump() for rn in sorted(output.basket, key=lambda r: r.rank)]
        ),
        valuation_check=output.valuation_check,
        exit_criteria=output.exit_criteria,
        model_tag=model_tag,
        prompt_version=prompt_version,
        created_at=clock,
    )
    repo.insert(conn, thesis)

    # The analyst's narrative objects are JUDGMENT assertions, clearly typed.
    judgment = Assertion(
        type=ProvenanceType.JUDGMENT,
        value={"thesis_id": thesis.id, "version": 1, "kind": "analyst_thesis_draft"},
        model_tag=model_tag,
        prompt_version=prompt_version,
        created_at=clock,
    )
    repo.insert_assertion(conn, judgment)

    for f in output.forecasts:
        repo.insert(
            conn,
            Forecast(
                thesis_id=thesis.id,
                statement=f.statement,
                probability=f.probability,
                horizon_end=f.horizon_end,
                resolution_criterion=f.resolution_criterion,
                created_by=AgentRole.RESEARCH_ANALYST,
                model_tag=model_tag,
                prompt_version=prompt_version,
                created_at=clock,
            ),
        )
    for m in output.milestones:
        repo.insert(
            conn,
            Milestone(
                thesis_id=thesis.id,
                description=m.description,
                falsification_criterion=m.falsification_criterion,
                status=MilestoneStatus.PENDING,
                created_at=clock,
            ),
        )
    conn.commit()
    return thesis, output
