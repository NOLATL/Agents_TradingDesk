"""Friday-night deep-dive report — one individual artifact per accepted theme
(consolidation spec: per-theme, never batched). Report v2: a sell-side-style
layout (recommendation first, stat block, exposure scoreboard, charts) whose
prose sections come from the Report Assembler's guard-validated narrative
(JUDGMENT assertion kind='report_narrative') with citations as markdown
footnotes resolving in the provenance appendix. Rendering is deterministic:
everything rehydrates from Postgres (the weekly cycle passes only the
thesis), so re-renders, repairs, and backfills never call an LLM. Any missing
narrative section falls back to the deterministic layout — loudly.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from analytics.momentum import QUARANTINE_KEY
from config.loader import SLICE
from data import repository as repo
from schemas.enums import ProvenanceType
from schemas.models import Assertion, Forecast, Milestone, Theme, Thesis

OUTPUT_DIR = Path(__file__).parent / "output" / "deepdive"

EXIT_ORDER = ("PRICE_STOP", "CAUSAL_BREAK", "VALUATION_EXIT", "TIME_STOP")

# Exposure-score badge thresholds (module constants so the scoreboard legend
# and any future config stay in one place).
SCORE_GREEN_MIN = 0.5
SCORE_YELLOW_MIN = 0.25
SPARK_GLYPHS = "▁▂▃▄▅▆▇█"
BODY_WORDS_TARGET = 4500
MILESTONE_ICONS = {"PENDING": "⬜", "MET": "✅", "FALSIFIED": "🟥"}
MARKER_RE = re.compile(r"\[\^A(\d+)\](?!:)")


# --------------------------------------------------------------------------- #
# Rehydration — everything the report needs, recovered from Postgres alone
# --------------------------------------------------------------------------- #

@dataclass
class DiveBundle:
    theme: Theme | None = None
    basket: list[dict] = field(default_factory=list)
    audit: dict | None = None            # reviewer_audit assertion value
    exec_summary: str | None = None
    narrative: dict | None = None        # report_narrative assertion value
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
    b.narrative = safe("narrative", lambda: _assertion_value(
        conn, "report_narrative", thesis.id))
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


def _refs(ids) -> str:
    return "".join(f"[^A{i}]" for i in ids)


def _score_badge(v: float | None) -> str:
    if v is None:
        return "—"
    badge = ("🟢" if v >= SCORE_GREEN_MIN
             else "🟡" if v >= SCORE_YELLOW_MIN else "🔴")
    filled = max(0, min(5, round(v * 5)))
    return f"{_fmt_value(v)} {badge} {'▰' * filled}{'▱' * (5 - filled)}"


def _spark(values: list[float]) -> str:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return SPARK_GLYPHS[3] * len(vals)
    scale = len(SPARK_GLYPHS) - 1
    return "".join(
        SPARK_GLYPHS[round((v - lo) / (hi - lo) * scale)] for v in vals)


def _mermaid_xychart(title: str, labels: list[str], series: list[tuple[str, str, list[float]]],
                     y_range: tuple[float, float] | None = None) -> list[str]:
    """series: (kind 'bar'|'line', name, values). Renders in Obsidian; shows
    as a code block elsewhere — accepted trade (user decision)."""
    out = ["```mermaid", "xychart-beta", f'    title "{title}"',
           "    x-axis [" + ", ".join(str(x) for x in labels) + "]"]
    if y_range:
        out.append(f"    y-axis {y_range[0]:g} --> {y_range[1]:g}")
    for kind, _name, values in series:
        out.append(f"    {kind} [" + ", ".join(f"{v:g}" for v in values) + "]")
    out.append("```")
    return out


def _macro_series(macro: list[Assertion]) -> dict[str, list[Assertion]]:
    series: dict[str, list[Assertion]] = {}
    for a in macro:
        series.setdefault(a.value.get("series_id", "?"), []).append(a)
    return {sid: sorted(obs, key=lambda a: a.value.get("date", ""))
            for sid, obs in sorted(series.items())}


def _narrative_sections(b: DiveBundle) -> tuple[dict, dict[str, str]]:
    """(narrative dict, company narratives by symbol) — empty when absent."""
    nar = (b.narrative or {}).get("narrative") or {}
    companies = {c.get("symbol"): c.get("narrative")
                 for c in nar.get("company_narratives", []) if c.get("narrative")}
    return nar, companies


def _fallback_callout(what: str) -> list[str]:
    return [f"> [!warning] Narrative unavailable for {what} — deterministic"
            " fallback rendering.", ""]


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def render(conn, thesis: Thesis, week_key: str, packet_doc_id: int | None) -> str:
    b = rehydrate(conn, thesis)
    nar, company_nar = _narrative_sections(b)
    rejected = thesis.status.value == "REJECTED"
    verdict = (b.audit or {}).get("verdict")
    add = (lines := []).append

    # 1 — header: recommendation first, then the stat block
    add(f"# Deep dive {week_key}: {thesis.title}")
    add("")
    if rejected:
        add("**🔴 REVIEWER REJECTED — no action needed**")
    else:
        add("**🟢 REVIEWED — awaiting your accept/reject**")
    add("")
    n_claims, n_metrics, n_macro = len(b.claims), len(b.metrics), len(b.macro)
    top = b.basket[0].get("symbol") if b.basket else "—"
    add("| Verdict | Names | Top pick | Forecasts | Milestones | Evidence |")
    add("|---|---|---|---|---|---|")
    add(f"| {'🔴' if rejected else '🟢'} {verdict or thesis.status.value}"
        f" | {len(b.basket)} | {top} | {len(b.forecasts)}"
        f" | {len(b.milestones)}"
        f" | {n_claims} claims · {n_metrics} metrics · {n_macro} macro |")
    add("")
    add(f"- thesis: #{thesis.id} v{thesis.version} ({thesis.status.value})"
        f" · theme: {thesis.strategy_tag}"
        + (f" (source: {b.theme.source.value.lower()})" if b.theme else ""))
    if packet_doc_id:
        add(f"- evidence packet: documents #{packet_doc_id} (immutable —"
            f" view: `uv run python -m ops.approve packet {packet_doc_id}`)")
    add(f"- model: `{thesis.model_tag}` · prompt {thesis.prompt_version[:12]}…")
    if b.narrative:
        body_words = b.narrative.get("body_words")
        add(f"- narrative: ~{body_words} body words"
            f" · Sonnet audit: {b.narrative.get('audit', '?')}")
        if isinstance(body_words, int) and body_words < BODY_WORDS_TARGET:
            b.notes.append(f"narrative body {body_words} words is below the"
                           f" {BODY_WORDS_TARGET}-word target")
        for name in b.narrative.get("dropped_sections", []):
            b.notes.append(f"narrative section '{name}' was dropped —"
                           " deterministic fallback below")
    else:
        b.notes.append("no narrative recorded for this dive — deterministic"
                       " layout throughout")
    if b.notes:
        add("")
        add("> [!warning] Degraded rendering")
        for note in b.notes:
            add(f"> - ⚠ {note}")
    if rejected:
        add("")
        add("> [!warning] ⚠ **REVIEWER REJECTED this draft** — no decision"
            " needed. The theme stays ACCEPTED and the dive retries next"
            " Friday; close the thread with `theme reject` if the idea itself"
            " is dead. The reviewer's challenges are in the Independent"
            " review section below.")

    # 2 — executive summary / lede
    add("")
    add("## Executive summary (LLM-written, JUDGMENT — adds no claims)")
    add("")
    add(nar.get("lead") or b.exec_summary
        or "(no executive summary recorded for this dive)")

    # 3 — investment thesis
    add("")
    add("## Investment thesis")
    add("")
    if nar.get("thesis_narrative"):
        add(nar["thesis_narrative"])
        add("")
        add("> [!note]- Raw causal chain (analyst JUDGMENT)")
        add(f"> {' '.join(thesis.causal_chain.split())}")
    else:
        add(thesis.causal_chain)

    # 4 — theme & macro backdrop
    add("")
    add("## Theme & macro backdrop")
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
    if nar.get("macro_narrative"):
        add("")
        add(nar["macro_narrative"])
    series = _macro_series(b.macro)
    if series:
        add("")
        add("| Series | Obs | Latest | Value | Min | Max | Trend | Refs |")
        add("|---|---|---|---|---|---|---|---|")
        for sid, obs in series.items():
            vals = [a.value.get("value") for a in obs]
            clean = [v for v in vals if v is not None]
            latest = obs[-1]
            add(f"| {sid} | {len(obs)} | {latest.value.get('date', '?')}"
                f" | {_fmt_value(latest.value.get('value'))}"
                f" | {_fmt_value(min(clean) if clean else None)}"
                f" | {_fmt_value(max(clean) if clean else None)}"
                f" | {_spark(vals)} | {_refs([obs[0].id, latest.id])} |")
        add("")
        add("*Macro observations are SOURCE_FACTs from cached FRED documents;"
            " every observation resolves in the provenance appendix.*")
        for sid, obs in series.items():
            pts = [(a.value.get("date", ""), a.value.get("value")) for a in obs
                   if a.value.get("value") is not None]
            if len(pts) >= 2:
                add("")
                lines.extend(_mermaid_xychart(
                    f"{sid} (last {len(pts)} observations)",
                    [d[5:7] + "/" + d[2:4] for d, _ in pts],
                    [("line", sid, [float(v) for _, v in pts])]))

    # 5 — exposure scoreboard
    add("")
    add("## Exposure scoreboard (measured exposure only — invariant 5)")
    add("")
    if b.basket:
        metrics_by_sym: dict[str, dict[str, Assertion]] = {}
        for a in b.metrics:
            metrics_by_sym.setdefault(
                a.value.get("symbol"), {})[a.value.get("metric")] = a
        add(f"Exposure badge: 🟢 ≥ {SCORE_GREEN_MIN} · 🟡 ≥ {SCORE_YELLOW_MIN}"
            " · 🔴 below.")
        add("")
        add("| Rank | Symbol | Exposure | Emphasis | Other metrics |"
            " Headline figure | Refs |")
        add("|---|---|---|---|---|---|---|")
        for r in b.basket:
            sym = r.get("symbol")
            m = metrics_by_sym.get(sym, {})
            score = m.get("exposure_score")
            emphasis = m.get("emphasis_total")
            others = " · ".join(
                f"{name}={_fmt_value(a.value.get('value'))}"
                for name, a in m.items()
                if name not in ("exposure_score", "emphasis_total"))
            headline = "—"
            for a in b.claims:
                v = a.value
                if v.get("symbol") == sym and v.get("figure_name"):
                    unit = (v.get("figure_unit") or "").replace("USD_", "$")
                    headline = (f"{v['figure_name']}"
                                f" {_fmt_value(v.get('figure_value'))} {unit}")
                    break
            ref_ids = list(r.get("supporting_assertion_ids", []))
            add(f"| {r.get('rank')} | **{sym}**"
                f" | {_score_badge(float(score.value['value']) if score and score.value.get('value') is not None else None)}"
                f" | {_fmt_value(emphasis.value.get('value')) if emphasis else '—'}"
                f" | {others or '—'} | {_cell(headline, 60)}"
                f" | {_refs(ref_ids)} |")
        scored = [(r.get("symbol"),
                   metrics_by_sym.get(r.get("symbol"), {}).get("exposure_score"))
                  for r in b.basket]
        scored = [(s, float(a.value["value"])) for s, a in scored
                  if a and a.value.get("value") is not None]
        if len(scored) >= 2:
            add("")
            lines.extend(_mermaid_xychart(
                "Measured exposure score by name",
                [s for s, _ in scored],
                [("bar", "exposure_score", [v for _, v in scored])],
                y_range=(0, 1)))
    else:
        add("(no basket recorded)")

    # 6 — company analysis
    add("")
    add("## Company analysis")
    for r in b.basket:
        sym = r.get("symbol")
        add("")
        add(f"### #{r.get('rank')} {sym}")
        add("")
        if company_nar.get(sym):
            add(company_nar[sym])
            continue
        if b.narrative:
            lines.extend(_fallback_callout(f"{sym}"))
        add(_cell(r.get("justification"), 1000)
            + " " + _refs(r.get("supporting_assertion_ids", [])))
        sym_claims = [a for a in b.claims if a.value.get("symbol") == sym]
        if sym_claims:
            add("")
            add("Evidence on file (SOURCE_FACT):")
            add("")
            for a in sym_claims:
                add(f"- {a.value.get('claim')} [^A{a.id}]")
        if not sym_claims:
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
    if nar.get("valuation_discussion"):
        add("")
        add("### Valuation discussion (Report Assembler JUDGMENT)")
        add("")
        add(nar["valuation_discussion"])

    # 8 — forecasts
    add("")
    add("## Forecasts (the measurement instrument)")
    add("")
    add("Probabilities are JUDGMENT by construction — scored (Brier), never"
        " acted on as quantities. P analyst is pre-review; P final reflects"
        " reviewer revisions.")
    add("")
    add("| # | Statement | P analyst | P final | Δ | Horizon |"
        " Resolution criterion |")
    add("|---|---|---|---|---|---|---|")
    for i, f in enumerate(b.forecasts):
        prior = b.prior_forecasts[i].probability if i < len(b.prior_forecasts) else None
        delta = ("—" if prior is None or prior == f.probability
                 else "▼" if f.probability < prior else "▲")
        prior_txt = f"{prior:.2f}" if prior is not None else "—"
        add(f"| {i + 1} | {_cell(f.statement)} | {prior_txt} | {f.probability:.2f} |"
            f" {delta} | {f.horizon_end} | {_cell(f.resolution_criterion)} |")
    if not b.forecasts:
        add("| — | (no forecasts recorded) | | | | | |")
    priors = [b.prior_forecasts[i].probability if i < len(b.prior_forecasts)
              else None for i in range(len(b.forecasts))]
    if b.forecasts and any(p is not None for p in priors):
        add("")
        lines.extend(_mermaid_xychart(
            "Forecast probabilities — analyst (line) vs post-review (bar)",
            [f"F{i + 1}" for i in range(len(b.forecasts))],
            [("bar", "final", [f.probability for f in b.forecasts]),
             ("line", "analyst", [p if p is not None else 0 for p in priors])],
            y_range=(0, 1)))
    if nar.get("forecast_rationale"):
        add("")
        add("### Forecast rationale (Report Assembler JUDGMENT)")
        add("")
        add(nar["forecast_rationale"])

    # 9 — independent review & risk (always shown)
    add("")
    add("## Independent review & risk")
    add("")
    if b.audit:
        if verdict == "ACTIVATE":
            add("> [!success] Reviewer verdict: ACTIVATE — stops at REVIEWED;"
                " only your `thesis accept` activates.")
        else:
            add(f"> [!warning] Reviewer verdict: {verdict or '?'}")
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
        add("| Ref | Symbol | Metric | Value | Function (version) |")
        add("|---|---|---|---|---|")
        for a in b.quarantined:
            add(f"| {_refs([a.id])} | {a.value.get('symbol')}"
                f" | {a.value.get('metric')}"
                f" | {_fmt_value(a.value.get('value'))}"
                f" | `{a.function_name}` ({a.code_version}) |")
    add("")
    add("### Do-nothing case")
    add("")
    add(thesis.do_nothing_case or "(none recorded)")
    if nar.get("risk_discussion"):
        add("")
        add("### Risk discussion (Report Assembler JUDGMENT)")
        add("")
        add(nar["risk_discussion"])

    # 10 — milestones & monitoring plan
    add("")
    add("## Milestones & monitoring plan")
    add("")
    add("The daily monitor checks reversal against these; FALSIFIED escalates"
        " toward CAUSAL_BREAK.")
    add("")
    if b.milestones:
        add("| Status | Milestone | Falsified if |")
        add("|---|---|---|")
        for m in b.milestones:
            icon = MILESTONE_ICONS.get(m.status.value, m.status.value)
            add(f"| {icon} {m.status.value} | {_cell(m.description)}"
                f" | {_cell(m.falsification_criterion)} |")
    else:
        add("- (none — monitoring has nothing falsifiable to check)")
    if nar.get("monitoring_outlook"):
        add("")
        add("### Monitoring outlook (Report Assembler JUDGMENT)")
        add("")
        add(nar["monitoring_outlook"])

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

    # 13 — provenance appendix (footnote definitions)
    body = "\n".join(lines)
    return body + "\n" + _footnote_appendix(conn, body, b) + "\n"


def _footnote_appendix(conn, body: str, b: DiveBundle) -> str:
    add = (out := [""]).append
    add("## Provenance appendix")
    add("")
    add("Every citation above resolves below. SOURCE_FACT → verbatim"
        " citation; COMPUTED → named function + code version; JUDGMENT →"
        " labeled model opinion.")
    add("")
    fetched = {a.id: a for a in b.claims + b.metrics + b.quarantined + b.macro}
    used_ids = sorted({int(x) for x in MARKER_RE.findall(body)})
    for aid in used_ids:
        a = fetched.get(aid) or repo.fetch_one(conn, Assertion, "id = %s", (aid,))
        if a is None:
            add(f"[^A{aid}]: MISSING — provenance break")
            continue
        if a.type is ProvenanceType.SOURCE_FACT:
            span = " ".join((a.citation_span or "").split())[:200]
            resolution = f"SOURCE_FACT — doc {a.source_doc_id}: “{span}”"
        elif a.type is ProvenanceType.COMPUTED:
            resolution = f"COMPUTED — `{a.function_name}` @ {a.code_version}"
        else:
            resolution = f"{a.type.value} — {a.type.value.lower()} ({a.model_tag})"
        add(f"[^A{aid}]: {resolution}")
    uncited = [a for aid, a in sorted(fetched.items()) if aid not in used_ids]
    if uncited:
        add("")
        add(f"> [!note]- Additional evidence on file ({len(uncited)}"
            " assertions, collected but not cited above)")
        for a in uncited:
            v = a.value
            label = (v.get("claim") or v.get("metric")
                     or (f"{v.get('series_id')} @ {v.get('date')}"
                         if v.get("series_id") else "assertion"))
            add(f"> - A{a.id} — {v.get('symbol') or ''}"
                f" {' '.join(str(label).split())[:140]}")
    return "\n".join(out)


def has_narrative(conn, thesis_id: int) -> bool:
    return _assertion_value(conn, "report_narrative", thesis_id) is not None


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
