"""Report Assembler (role 4): renders validated objects to markdown.

Structural guarantee that it adds no claims: every substantive section is
template-rendered from persisted objects by reporting/packet.py. The single
LLM call writes only a clearly-labeled executive summary of the provided
objects, stored as a JUDGMENT assertion.
"""

from datetime import datetime

from agents import llm
from agents.state import AnalystOutput, EvidencePacket, ReportSummary, ReviewerOutput
from data import repository as repo
from schemas.enums import AgentRole, ProvenanceType
from schemas.models import Assertion, Thesis

REPORTER_SYSTEM_PROMPT = """You are the Report Assembler of a research desk. Summarize the provided
thesis, review, and forecast objects in 3-5 plain sentences for a human reader.
You MUST NOT introduce any fact, number, company, or judgment that is not in the provided
objects. You are a compressor, not an author."""


def summarize(
    conn,
    packet: EvidencePacket,
    thesis: Thesis,
    analyst_output: AnalystOutput,
    reviewer_output: ReviewerOutput,
    clock: datetime,
) -> str:
    output = llm.structured_call(
        conn,
        role=AgentRole.REPORT_ASSEMBLER,
        experiment_id=packet.experiment_id,
        system_prompt=REPORTER_SYSTEM_PROMPT,
        user_prompt=(
            f"THESIS (v{thesis.version}, status {thesis.status.value}):\n"
            f"{thesis.causal_chain}\n\n"
            f"BASKET:\n{analyst_output.model_dump_json(include={'basket'})}\n\n"
            f"REVIEW:\n{reviewer_output.model_dump_json()}\n"
        ),
        schema=ReportSummary,
        temperature=0.2,
    )
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.JUDGMENT,
            value={
                "thesis_id": thesis.id,
                "kind": "report_executive_summary",
                "text": output.executive_summary,
            },
            model_tag=llm.LLM_CFG["model_tag"] + "@" + llm.cached_digest(),
            prompt_version=llm._sha(REPORTER_SYSTEM_PROMPT),
            created_at=clock,
        ),
    )
    conn.commit()
    return output.executive_summary
