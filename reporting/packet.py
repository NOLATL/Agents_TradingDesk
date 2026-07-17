"""Decision packet renderer — deterministic markdown from persisted objects.

Adds no claims: every line traces to a stored object; the provenance appendix
resolves every assertion id to a citation span (SOURCE_FACT), a named
function+version (COMPUTED), or a labeled JUDGMENT.
"""

import json
from datetime import datetime
from pathlib import Path

from agents.state import AnalystOutput, EvidencePacket, ReviewerOutput
from config.loader import SLICE
from data import repository as repo
from schemas.enums import ProvenanceType
from schemas.models import Assertion, Forecast, Thesis

OUTPUT_DIR = Path(__file__).parent / "output"


def render(
    conn,
    packet: EvidencePacket,
    thesis: Thesis,
    analyst_output: AnalystOutput,
    reviewer_output: ReviewerOutput,
    executive_summary: str,
    clock: datetime,
) -> str:
    forecasts = repo.fetch(conn, Forecast, "thesis_id = %s ORDER BY id", (thesis.id,))
    basket = sorted(analyst_output.basket, key=lambda r: r.rank)

    lines: list[str] = []
    add = lines.append
    add(f"# Decision Packet — {thesis.title}")
    add("")
    add(f"*Generated {clock.isoformat()} · thesis v{thesis.version} ({thesis.status.value})"
        f" · experiment {packet.experiment_id} · strategy_tag `{thesis.strategy_tag}`*")
    add("")
    add("> **No orders are proposed in Stage 1.** This packet is the evidence-to-thesis")
    add("> vertical slice output; sizing and orders arrive with the Stage 2 simulator.")
    add("")
    add("## Executive summary (LLM-written, JUDGMENT — adds no claims)")
    add("")
    add(executive_summary)
    add("")
    add("## Causal chain")
    add("")
    add(thesis.causal_chain)
    add("")
    add("## Exposure basket (ranked by measured exposure only — invariant 5)")
    add("")
    add("| Rank | Symbol | Justification | Supporting assertions |")
    add("|---|---|---|---|")
    for r in basket:
        refs = ", ".join(f"A{i}" for i in r.supporting_assertion_ids)
        add(f"| {r.rank} | {r.symbol} | {r.justification} | {refs} |")
    add("")
    add("### Measured exposure metrics")
    add("")
    add("| Assertion | Symbol | Metric | Value | Function (version) |")
    add("|---|---|---|---|---|")
    for m in packet.metrics:
        add(f"| A{m.assertion_id} | {m.symbol} | {m.name} | {m.value} |"
            f" `{m.function_name}` ({m.code_version}) |")
    add("")
    add("## Valuation check")
    add("")
    add(thesis.valuation_check)
    add("")
    add("## Forecasts (the measurement instrument)")
    add("")
    add("| # | Statement | P | Horizon | Resolution criterion |")
    add("|---|---|---|---|---|")
    for i, f in enumerate(forecasts, 1):
        add(f"| {i} | {f.statement} | {f.probability:.2f} | {f.horizon_end} |"
            f" {f.resolution_criterion} |")
    add("")
    add("## Exit criteria (all four types, defined at creation)")
    add("")
    for key in ("PRICE_STOP", "CAUSAL_BREAK", "VALUATION_EXIT", "TIME_STOP"):
        add(f"- **{key}**: {thesis.exit_criteria[key]}")
    add("")
    add("## Independent review")
    add("")
    add(f"**Verdict: {reviewer_output.verdict}**")
    add("")
    add("### Challenges")
    for c in reviewer_output.challenges:
        add(f"- {c}")
    add("")
    add("### Is price driving the narrative? (reviewer-only quarantined metrics)")
    add("")
    add(reviewer_output.price_narrative_check)
    add("")
    add("### Do-nothing case")
    add("")
    add(reviewer_output.do_nothing_case)
    add("")
    add("## Provenance appendix")
    add("")
    add("Every assertion id used above resolves below. SOURCE_FACT → verbatim citation;")
    add("COMPUTED → named function + code version; JUDGMENT → labeled model opinion.")
    add("")
    add("| Assertion | Type | Resolution |")
    add("|---|---|---|")
    used_ids = sorted(
        {i for r in basket for i in r.supporting_assertion_ids}
        | {c.assertion_id for c in packet.claims}
        | {m.assertion_id for m in packet.metrics}
        | {p.assertion_id for p in packet.macro}
    )
    for aid in used_ids:
        a = repo.fetch_one(conn, Assertion, "id = %s", (aid,))
        if a is None:
            add(f"| A{aid} | ? | MISSING — provenance break |")
            continue
        if a.type is ProvenanceType.SOURCE_FACT:
            span = (a.citation_span or "").replace("|", "\\|")[:160]
            resolution = f"doc {a.source_doc_id}: “{span}”"
        elif a.type is ProvenanceType.COMPUTED:
            resolution = f"`{a.function_name}` @ {a.code_version}"
        else:
            resolution = f"{a.type.value.lower()} ({a.model_tag})"
        add(f"| A{aid} | {a.type.value} | {resolution} |")
    add("")
    add("---")
    add(f"*Model: `{thesis.model_tag}` · prompt {thesis.prompt_version[:12]}…"
        f" · all runs logged to `agent_runs`.*")
    return "\n".join(lines)


def write(content: str, clock: datetime) -> list[Path]:
    stamp = clock.strftime("%Y-%m-%d")
    name = f"decision-packet-{SLICE['theme']}-{stamp}.md"
    paths = []
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    local = OUTPUT_DIR / name
    local.write_text(content)
    paths.append(local)
    vault = Path(SLICE["vault_dir"]) / "packets"
    vault.mkdir(parents=True, exist_ok=True)
    mirror = vault / name
    mirror.write_text(content)
    paths.append(mirror)
    return paths
