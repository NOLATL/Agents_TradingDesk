"""Independent Reviewer (role 3): audits the thesis.

Sees ONLY the evidence packet and the analyst's OUTPUT (never its reasoning
trace) to reduce correlated framing. Explicitly receives the quarantined
momentum/volatility metrics for exactly one purpose: the 'is price action
driving the narrative?' check. Produces challenges + do-nothing case and the
post-review thesis version (v2), preserving v1 (pre/post both persisted).
A REJECT verdict logs the hypothesis to research_genealogy.
"""

from datetime import datetime

from agents import llm
from agents.state import AnalystOutput, EvidencePacket, ReviewerOutput
from data import repository as repo
from schemas.enums import AgentRole, ProvenanceType, ThesisStatus
from schemas.models import Assertion, Forecast, ResearchGenealogy, Thesis

REVIEWER_SYSTEM_PROMPT = """You are the Independent Reviewer of a thematic equity research desk.
You AUDIT a thesis you did not write. You are not a co-author and not a cheerleader —
and not merely a pessimist: your challenges must be specific and evidence-based.

Your jobs:
1. Hunt for disconfirming evidence in the packet the analyst may have discounted.
2. Challenge the exposure mapping: do the cited metrics actually establish exposure purity?
3. Challenge causal timing: could the chain be right but the window wrong?
4. Price-narrative check: you are given price-derived metrics (momentum, volatility) that the
   analyst could not see. Ask: does the thesis read like a story reverse-engineered from what
   has already moved? Say so plainly either way.
5. Write the do-nothing case: the strongest honest argument that taking no position is correct.
   "Evidence insufficient, take no position" is a valid, rewarded output.
6. Verdict: ACTIVATE only if the thesis is evidence-grounded, falsifiable, and its forecasts
   resolvable; otherwise REJECT with the controlling reason first in your challenges."""


def review(
    conn,
    packet: EvidencePacket,
    thesis_v1: Thesis,
    analyst_output: AnalystOutput,
    clock: datetime,
) -> tuple[Thesis, ReviewerOutput]:
    quarantined_lines = "\n".join(
        f"  {m.symbol} {m.name} = {m.value}" for m in packet.quarantined_metrics
    ) or "  (none available)"
    evidence_lines = "\n".join(
        f"  [A{c.assertion_id}] {c.symbol}: {c.claim}" for c in packet.claims
    )
    metric_lines = "\n".join(
        f"  [A{m.assertion_id}] {m.symbol} {m.name} = {m.value}" for m in packet.metrics
    )
    analyst_view = analyst_output.model_dump_json(indent=1)

    output = llm.structured_call(
        conn,
        role=AgentRole.INDEPENDENT_REVIEWER,
        experiment_id=packet.experiment_id,
        system_prompt=REVIEWER_SYSTEM_PROMPT,
        user_prompt=(
            f"EVIDENCE PACKET (facts):\n{evidence_lines}\n\n"
            f"MEASURED METRICS:\n{metric_lines}\n\n"
            f"PRICE-DERIVED METRICS (for the price-narrative check ONLY — the analyst never saw these):\n"
            f"{quarantined_lines}\n\n"
            f"ANALYST OUTPUT (structured, no reasoning trace):\n{analyst_view}\n\n"
            f"Simulation clock: {clock.date().isoformat()}. Audit this thesis."
        ),
        schema=ReviewerOutput,
        temperature=0.3,
    )
    if output.verdict not in ("ACTIVATE", "REJECT"):
        raise llm.ModelOutputRejected(f"reviewer verdict {output.verdict!r} not ACTIVATE|REJECT")

    model_tag = llm.LLM_CFG["model_tag"] + "@" + llm.cached_digest()
    prompt_version = llm._sha(REVIEWER_SYSTEM_PROMPT)

    # Post-review version (v2) — v1 stays untouched (pre/post both persisted).
    thesis_v2 = Thesis(
        experiment_id=thesis_v1.experiment_id,
        title=thesis_v1.title,
        strategy_tag=thesis_v1.strategy_tag,
        status=ThesisStatus.ACTIVE if output.verdict == "ACTIVATE" else ThesisStatus.REJECTED,
        version=thesis_v1.version + 1,
        parent_version_id=thesis_v1.id,
        causal_chain=thesis_v1.causal_chain,
        exposure_basket_approach=thesis_v1.exposure_basket_approach,
        valuation_check=thesis_v1.valuation_check,
        exit_criteria=thesis_v1.exit_criteria,
        do_nothing_case=output.do_nothing_case,
        model_tag=model_tag,
        prompt_version=prompt_version,
        created_at=clock,
    )
    repo.insert(conn, thesis_v2)

    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.JUDGMENT,
            value={
                "thesis_id": thesis_v2.id,
                "kind": "reviewer_audit",
                "verdict": output.verdict,
                "challenges": output.challenges,
                "price_narrative_check": output.price_narrative_check,
            },
            model_tag=model_tag,
            prompt_version=prompt_version,
            created_at=clock,
        ),
    )

    # Re-point forecasts at the surviving version; apply challenged revisions.
    forecasts = repo.fetch(conn, Forecast, "thesis_id = %s", (thesis_v1.id,))
    for idx, f in enumerate(forecasts):
        revised = output.forecast_probability_revisions.get(str(idx))
        new_probability = revised if revised is not None else f.probability
        repo.insert(
            conn,
            Forecast(
                thesis_id=thesis_v2.id,
                statement=f.statement,
                probability=new_probability,
                horizon_end=f.horizon_end,
                resolution_criterion=f.resolution_criterion,
                created_by=AgentRole.INDEPENDENT_REVIEWER,
                model_tag=model_tag,
                prompt_version=prompt_version,
                created_at=clock,
            ),
        )

    repo.insert(
        conn,
        ResearchGenealogy(
            experiment_id=packet.experiment_id,
            hypothesis_title=thesis_v1.title,
            thesis_id=thesis_v2.id,
            rejected=output.verdict == "REJECT",
            rejection_reason=output.challenges[0] if output.verdict == "REJECT" else None,
            variant_count=1,
            evidence_added_after_price_observation=False,  # blinding held; reviewer saw price only for the check
            created_at=clock,
        ),
    )
    conn.commit()
    return thesis_v2, output
