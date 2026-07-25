"""Decision packet — immutable decision evidence, cached in `documents`.

The packet is a machine artifact, not a report: it is rendered once per dive
and cached as an immutable `documents` row so orders can reference exactly
what the human saw (invariant 6) and research replays from stored artifacts
(invariant 3). The human-facing per-theme report is the deep dive
(`reporting/deepdive.py`), which supersets every packet section and
cross-links the cached row. No packet files are written anywhere.

Adds no claims: every line traces to a stored object; the provenance appendix
resolves every assertion id to a citation span (SOURCE_FACT), a named
function+version (COMPUTED), or a labeled JUDGMENT.

This module owns the packet's whole lifecycle: render → cache → latest_doc_id.
"""

import hashlib
from datetime import datetime

from agents.state import AnalystOutput, EvidencePacket, ReviewerOutput
from config.loader import SLICE
from data import repository as repo
from schemas.enums import ProvenanceType
from schemas.models import Assertion, Document, Forecast, Thesis


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


def cache(conn, content: str, clock: datetime, theme: str | None = None,
          commit: bool = True) -> int:
    """Cache the rendered packet as an immutable documents row and return its
    id. Idempotent: an identical packet (by sha256) reuses the existing row.

    The name in retrieval_url carries the run's theme — the packet↔thesis
    linkage (latest_doc_id) matches on it, for both these `desk://` rows and
    the legacy `file://` rows cached before packets became DB-only."""
    stamp = clock.strftime("%Y-%m-%d")
    name = f"decision-packet-{theme or SLICE['theme']}-{stamp}.md"
    raw = content.encode("utf-8")
    h = hashlib.sha256(raw).hexdigest()
    existing = repo.document_by_hash(conn, h)
    if existing:
        return existing.id
    doc = Document(
        content_hash=h, raw_bytes=raw, extracted_text=content,
        retrieval_url=f"desk://packets/{name}", source_name="desk_packet",
        ingestion_time=clock, first_eligible_decision_time=clock,
    )
    doc_id = repo.insert_document(conn, doc)
    if commit:
        conn.commit()
    return doc_id


def latest_doc_id(conn, strategy_tag: str) -> int | None:
    """Newest cached decision packet for this theme — the evidence the order
    gate shows must be the packet of THIS theme's dive, not whichever theme
    happened to run last."""
    row = conn.execute(
        "SELECT id FROM documents WHERE source_name = 'desk_packet'"
        " AND retrieval_url LIKE %s ORDER BY ingestion_time DESC, id DESC LIMIT 1",
        (f"%decision-packet-{strategy_tag}-%",),
    ).fetchone()
    return row[0] if row else None
