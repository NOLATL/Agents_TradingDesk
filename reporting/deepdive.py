"""Friday-night deep-dive report — one individual artifact per accepted theme
(consolidation spec: per-theme, never batched). Research-report grade: every
section is rendered from persisted objects rehydrated from Postgres (the
weekly cycle passes only the thesis, so a re-render is always possible), LLM
prose appears only under clearly labeled JUDGMENT headings, and every
assertion id used resolves in the provenance appendix. Referenced from the
daily briefing; not duplicated inside it.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from analytics.momentum import QUARANTINE_KEY
from config.loader import SLICE
from data import repository as repo
from schemas.enums import ProvenanceType
from schemas.models import Assertion, Forecast, Milestone, Theme, Thesis

OUTPUT_DIR = Path(__file__).parent / "output" / "deepdive"

EXIT_ORDER = ("PRICE_STOP", "CAUSAL_BREAK", "VALUATION_EXIT", "TIME_STOP")


# --------------------------------------------------------------------------- #
# Rehydration — everything the report needs, recovered from Postgres alone
# --------------------------------------------------------------------------- #

@dataclass
class DiveBundle:
    theme: Theme | None = None
    basket: list[dict] = field(default_factory=list)
    audit: dict | None = None            # reviewer_audit assertion value
    exec_summary: str | None = None
    claims: list[Assertion] = field(default_factory=list)      # SOURCE_FACT
    metrics: list[Assertion] = field(default_factory=list)     # COMPUTED, analyst-visible
    quarantined: list[Assertion] = field(default_factory=list) # COMPUTED, reviewer-only
    macro: list[Assertion] = field(default_factory=list)       # SOURCE_FACT (FRED)
    forecasts: list[Forecast] = field(default_factory=list)          # this version
    prior_forecasts: list[Forecast] = field(default_factory=list)    # pre-review (v1)
    milestones: list[Milestone] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # loud degradation notes


def _assertion_value(conn, kind: str, thesis_id: int) -> dict | None:
    row = conn.execute(
        "SELECT value FROM assertions WHERE value->>'kind' = %s"
        " AND (value->>'thesis_id')::int = %s ORDER BY id DESC LIMIT 1",
        (kind, thesis_id),
    ).fetchone()
    return row[0] if row else None


def _by_ids(conn, ids: list[int]) -> list[Assertion]:
    if not ids:
        return []
    return repo.fetch(conn, Assertion, "id = ANY(%s) ORDER BY id", (list(ids),))


def _evidence_upper_bound(conn, thesis: Thesis) -> int | None:
    """Smallest id among this dive's post-packet JUDGMENT assertions — every
    claim/metric/macro assertion of the dive was inserted before it."""
    ids = [thesis.id]
    if thesis.parent_version_id:
        ids.append(thesis.parent_version_id)
    row = conn.execute(
        "SELECT min(id) FROM assertions WHERE value->>'kind' IN"
        " ('analyst_thesis_draft', 'reviewer_audit', 'report_executive_summary')"
        " AND (value->>'thesis_id')::int = ANY(%s)",
        (ids,),
    ).fetchone()
    return row[0] if row else None


def rehydrate(conn, thesis: Thesis) -> DiveBundle:
    """Recover the full dive from Postgres. Every piece is optional: missing
    data degrades to a loud note, never an exception — a render failure inside
    the weekly cycle would fail the cycle after committed LLM work."""
    b = DiveBundle()

    def safe(label, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — degradation over cycle failure
            b.notes.append(f"{label} not recoverable ({e})")
            return None

    b.theme = safe("theme", lambda: repo.fetch_one(
        conn, Theme, "id = %s", (thesis.theme_id,)) if thesis.theme_id else None)
    b.basket = safe("basket", lambda: sorted(
        json.loads(thesis.exposure_basket_approach), key=lambda r: r.get("rank", 0)
    )) or []
    b.audit = safe("reviewer audit", lambda: _assertion_value(
        conn, "reviewer_audit", thesis.id))
    b.exec_summary = safe("executive summary", lambda: (
        _assertion_value(conn, "report_executive_summary", thesis.id) or {}
    ).get("text"))
    b.forecasts = safe("forecasts", lambda: repo.fetch(
        conn, Forecast, "thesis_id = %s ORDER BY id", (thesis.id,))) or []
    if thesis.parent_version_id:
        b.prior_forecasts = safe("pre-review forecasts", lambda: repo.fetch(
            conn, Forecast, "thesis_id = %s ORDER BY id",
            (thesis.parent_version_id,))) or []
    b.milestones = safe("milestones", lambda: repo.fetch(
        conn, Milestone, "thesis_id = %s ORDER BY id", (thesis.id,))) or []
    if not b.milestones and thesis.parent_version_id:
        # Pre-copy-era dives left milestones on v1; the monitor reads the
        # surviving version, so say so rather than render an empty section.
        b.milestones = safe("parent-version milestones", lambda: repo.fetch(
            conn, Milestone, "thesis_id = %s ORDER BY id",
            (thesis.parent_version_id,))) or []
        if b.milestones:
            b.notes.append("milestones recovered from pre-review version")

    safe("evidence packet", lambda: _rehydrate_evidence(conn, thesis, b))
    return b


def _rehydrate_evidence(conn, thesis: Thesis, b: DiveBundle) -> None:
    symbols = [r.get("symbol") for r in b.basket if r.get("symbol")]
    manifest = _assertion_value(conn, "evidence_packet_manifest", thesis.id)
    if manifest:
        claims = _by_ids(conn, manifest.get("claim_assertion_ids", []))
        computed = _by_ids(conn, manifest.get("metric_assertion_ids", []))
        b.macro = _by_ids(conn, manifest.get("macro_assertion_ids", []))
    else:
        # Window closure for pre-manifest dives: the run's clock stamps every
        # COMPUTED/macro assertion exactly; claims use wall-clock ≥ clock and
        # are bounded above by the dive's first post-packet JUDGMENT id.
        bound = _evidence_upper_bound(conn, thesis)
        if bound is None:
            b.notes.append("evidence packet not recoverable — pre-upgrade"
                           " artifact without closure anchor")
            return
        computed = repo.fetch(
            conn, Assertion,
            "type = 'COMPUTED' AND value ? 'metric' AND created_at = %s"
            " AND id < %s ORDER BY id", (thesis.created_at, bound))
        b.macro = repo.fetch(
            conn, Assertion,
            "type = 'SOURCE_FACT' AND value ? 'series_id' AND created_at = %s"
            " AND id < %s ORDER BY id", (thesis.created_at, bound))
        claims = repo.fetch(
            conn, Assertion,
            "type = 'SOURCE_FACT' AND value ? 'claim' AND created_at >= %s"
            " AND id < %s AND value->>'symbol' = ANY(%s) ORDER BY id",
            (thesis.created_at, bound, symbols or [""]))
    b.claims = claims
    b.metrics = [a for a in computed if not a.value.get(QUARANTINE_KEY)]
    b.quarantined = [a for a in computed if a.value.get(QUARANTINE_KEY)]


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #

def _cell(text, limit: int = 200) -> str:
    """One markdown table cell: collapse whitespace/newlines, truncate,
    escape pipes."""
    out = " ".join(str(text if text is not None else "").split())
    if len(out) > limit:
        out = out[: limit - 1] + "…"
    return out.replace("|", "\\|")


def _fmt_value(v) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return _cell(v, 40)
    return f"{f:,.4g}"


def _macro_rows(macro: list[Assertion]) -> list[tuple]:
    series: dict[str, list[Assertion]] = {}
    for a in macro:
        series.setdefault(a.value.get("series_id", "?"), []).append(a)
    rows = []
    for sid, obs in sorted(series.items()):
        obs = sorted(obs, key=lambda a: a.value.get("date", ""))
        vals = [a.value.get("value") for a in obs if a.value.get("value") is not None]
        latest = obs[-1]
        rows.append((
            sid, len(obs),
            latest.value.get("date", "?"), _fmt_value(latest.value.get("value")),
            _fmt_value(min(vals) if vals else None),
            _fmt_value(max(vals) if vals else None),
            f"A{obs[0].id}–A{latest.id}",
        ))
    return rows


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def render(conn, thesis: Thesis, week_key: str, packet_doc_id: int | None) -> str:
    b = rehydrate(conn, thesis)
    rejected = thesis.status.value == "REJECTED"
    add = (lines := []).append

    # 1 — header / metadata
    add(f"# Deep dive {week_key}: {thesis.title}")
    add("")
    add(f"- thesis: #{thesis.id} v{thesis.version} ({thesis.status.value})")
    add(f"- theme: {thesis.strategy_tag}"
        + (f" (source: {b.theme.source.value.lower()})" if b.theme else ""))
    if b.audit and b.audit.get("verdict"):
        add(f"- reviewer verdict: {b.audit['verdict']}")
    if packet_doc_id:
        add(f"- evidence packet: documents #{packet_doc_id} (immutable —"
            f" view: `uv run python -m ops.approve packet {packet_doc_id}`)")
    add(f"- model: `{thesis.model_tag}` · prompt {thesis.prompt_version[:12]}…")
    for note in b.notes:
        add(f"- ⚠ {note}")
    if rejected:
        add("")
        add("> ⚠ **REVIEWER REJECTED this draft** — no decision needed. The"
            " theme stays ACCEPTED and the dive retries next Friday; close the"
            " thread with `theme reject` if the idea itself is dead. The"
            " reviewer's challenges are in the Independent review section"
            " below.")

    # 2 — executive summary
    add("")
    add("## Executive summary (LLM-written, JUDGMENT — adds no claims)")
    add("")
    add(b.exec_summary or "(no executive summary recorded for this dive)")

    # 3 — investment thesis
    add("")
    add("## Investment thesis — causal chain (analyst JUDGMENT)")
    add("")
    add(thesis.causal_chain)

    # 4 — theme background & macro environment
    add("")
    add("## Theme background & macro environment")
    add("")
    if b.theme:
        add(f"**{b.theme.title}** — proposed {b.theme.proposed_on}"
            f" via {b.theme.source.value.lower()}"
            + (f", accepted {b.theme.accepted_on}" if b.theme.accepted_on else "")
            + ".")
        if b.theme.description:
            add("")
            add(b.theme.description)
        rc = b.theme.research_config or {}
        add("")
        add(f"- universe: {', '.join(rc.get('symbols') or []) or '(none)'}")
        if rc.get("emphasis_terms"):
            add(f"- emphasis terms: {', '.join(rc['emphasis_terms'])}")
        if rc.get("fred_series"):
            add(f"- macro series: {', '.join(rc['fred_series'])}")
    else:
        add("(no intake theme row — ad-hoc dive)")
    if b.macro:
        add("")
        add("| Series | Obs | Latest | Value | Min | Max | Assertions |")
        add("|---|---|---|---|---|---|---|")
        for row in _macro_rows(b.macro):
            add("| " + " | ".join(str(c) for c in row) + " |")
        add("")
        add("*Macro observations are SOURCE_FACTs from cached FRED documents;"
            " full list in the provenance appendix.*")

    # 5 — exposure basket
    add("")
    add("## Exposure basket (ranked by measured exposure only — invariant 5)")
    add("")
    if b.basket:
        add("| Rank | Symbol | Justification (analyst JUDGMENT) | Supporting assertions |")
        add("|---|---|---|---|")
        for r in b.basket:
            refs = ", ".join(f"A{i}" for i in r.get("supporting_assertion_ids", []))
            add(f"| {r.get('rank')} | {r.get('symbol')} |"
                f" {_cell(r.get('justification'), 300)} | {refs} |")
    else:
        add("(no basket recorded)")

    # 6 — company-by-company analysis
    add("")
    add("## Company-by-company analysis")
    for r in b.basket:
        sym = r.get("symbol")
        add("")
        add(f"### #{r.get('rank')} {sym}")
        add("")
        add(_cell(r.get("justification"), 1000))
        sym_metrics = [a for a in b.metrics if a.value.get("symbol") == sym]
        if sym_metrics:
            add("")
            add("| Assertion | Metric | Value | Function (version) |")
            add("|---|---|---|---|")
            for a in sym_metrics:
                add(f"| A{a.id} | {a.value.get('metric')} |"
                    f" {_fmt_value(a.value.get('value'))} |"
                    f" `{a.function_name}` ({a.code_version}) |")
        sym_claims = [a for a in b.claims if a.value.get("symbol") == sym]
        if sym_claims:
            add("")
            add("Evidence (SOURCE_FACT, verbatim citations):")
            add("")
            for a in sym_claims:
                quote = " ".join((a.citation_span or "").split())[:160]
                add(f"- **A{a.id}** {a.value.get('claim')}")
                add(f"  - doc {a.source_doc_id}: “{quote}”")
        if not sym_metrics and not sym_claims:
            add("")
            add("(no per-name evidence recoverable)")
    if not b.basket:
        add("")
        add("(no basket recorded — nothing to analyze per name)")

    # 7 — valuation
    add("")
    add("## Valuation check (analyst JUDGMENT, from given fundamentals only)")
    add("")
    add(thesis.valuation_check)

    # 8 — forecasts
    add("")
    add("## Forecasts (the measurement instrument)")
    add("")
    add("Probabilities are JUDGMENT by construction — scored (Brier), never"
        " acted on as quantities. P analyst is pre-review; P final reflects"
        " reviewer revisions.")
    add("")
    add("| # | Statement | P analyst | P final | Horizon | Resolution criterion |")
    add("|---|---|---|---|---|---|")
    for i, f in enumerate(b.forecasts):
        prior = (f"{b.prior_forecasts[i].probability:.2f}"
                 if i < len(b.prior_forecasts) else "—")
        add(f"| {i + 1} | {_cell(f.statement)} | {prior} | {f.probability:.2f} |"
            f" {f.horizon_end} | {_cell(f.resolution_criterion)} |")
    if not b.forecasts:
        add("| — | (no forecasts recorded) | | | | |")

    # 9 — independent review & risk (always shown)
    add("")
    add("## Independent review & risk")
    add("")
    if b.audit:
        verdict = b.audit.get("verdict", "?")
        add(f"**Reviewer verdict: {verdict}**" + (
            " (ACTIVATE stops at REVIEWED — only your `thesis accept`"
            " activates)" if verdict == "ACTIVATE" else ""))
        add("")
        add("### Challenges")
        for c in b.audit.get("challenges", []):
            add(f"- {c}")
        add("")
        add("### Is price driving the narrative?")
        add("")
        add(b.audit.get("price_narrative_check")
            or "(no price-narrative check recorded)")
    else:
        add("(no reviewer audit recoverable for this dive)")
    if b.quarantined:
        add("")
        add("Price-derived metrics (reviewer-only — barred from analyst"
            " context and basket construction, invariant 5):")
        add("")
        add("| Assertion | Symbol | Metric | Value | Function (version) |")
        add("|---|---|---|---|---|")
        for a in b.quarantined:
            add(f"| A{a.id} | {a.value.get('symbol')} | {a.value.get('metric')} |"
                f" {_fmt_value(a.value.get('value'))} |"
                f" `{a.function_name}` ({a.code_version}) |")
    add("")
    add("### Do-nothing case")
    add("")
    add(thesis.do_nothing_case or "(none recorded)")

    # 10 — milestones & monitoring plan
    add("")
    add("## Milestones & monitoring plan")
    add("")
    add("The daily monitor checks reversal against these; FALSIFIED escalates"
        " toward CAUSAL_BREAK.")
    add("")
    for m in b.milestones:
        add(f"- [{m.status.value}] {m.description}")
        add(f"  - falsified if: {m.falsification_criterion}")
    if not b.milestones:
        add("- (none — monitoring has nothing falsifiable to check)")

    # 11 — exit criteria
    add("")
    add("## Exit criteria (all four types, defined at creation)")
    add("")
    criteria = thesis.exit_criteria or {}
    for key in EXIT_ORDER:
        if key in criteria:
            add(f"- **{key}**: {criteria[key]}")
    for key in sorted(set(criteria) - set(EXIT_ORDER)):
        add(f"- **{key}**: {criteria[key]}")

    # 12 — decision block
    add("")
    add("---")
    if rejected:
        add("No decision needed — this draft never reached you. To stop the"
            f" weekly retries: `uv run python -m ops.approve theme reject"
            f" {thesis.strategy_tag} --rationale \"...\"`")
    else:
        add("Your decision (this thesis does nothing until you decide):")
        add("```")
        add(f"uv run python -m ops.approve thesis accept {thesis.id}")
        add(f"uv run python -m ops.approve thesis reject {thesis.id} --rationale \"...\"")
        add("```")

    # 13 — provenance appendix
    add("")
    add("## Provenance appendix")
    add("")
    add("Every assertion id used above resolves below. SOURCE_FACT → verbatim"
        " citation; COMPUTED → named function + code version; JUDGMENT →"
        " labeled model opinion.")
    add("")
    add("| Assertion | Type | Resolution |")
    add("|---|---|---|")
    fetched = {a.id: a for a in b.claims + b.metrics + b.quarantined + b.macro}
    used_ids = sorted(
        {i for r in b.basket for i in r.get("supporting_assertion_ids", [])}
        | set(fetched)
    )
    for aid in used_ids:
        a = fetched.get(aid) or repo.fetch_one(conn, Assertion, "id = %s", (aid,))
        if a is None:
            add(f"| A{aid} | ? | MISSING — provenance break |")
            continue
        if a.type is ProvenanceType.SOURCE_FACT:
            resolution = f"doc {a.source_doc_id}: “{_cell(a.citation_span, 160)}”"
        elif a.type is ProvenanceType.COMPUTED:
            resolution = f"`{a.function_name}` @ {a.code_version}"
        else:
            resolution = f"{a.type.value.lower()} ({a.model_tag})"
        add(f"| A{aid} | {a.type.value} | {resolution} |")

    add("")
    return "\n".join(lines)


def expected_path(week_key: str, strategy_tag: str) -> Path:
    return OUTPUT_DIR / f"{week_key}-{strategy_tag}.md"


def write(conn, thesis: Thesis, week_key: str,
          packet_doc_id: int | None = None) -> list[Path]:
    content = render(conn, thesis, week_key, packet_doc_id)
    name = f"{week_key}-{thesis.strategy_tag}.md"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    local = OUTPUT_DIR / name
    local.write_text(content)
    vault = Path(SLICE["vault_dir"]) / "reports" / "deepdive" / name
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(content)
    return [local, vault]
