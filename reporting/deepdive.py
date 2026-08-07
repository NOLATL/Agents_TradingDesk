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
import sys
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


def _gauge(pos) -> str:
    """0..1 → `▰▰▱▱▱ 38%` range gauge (52wk position, percentiles)."""
    if pos is None:
        return "—"
    filled = max(0, min(5, round(float(pos) * 5)))
    return f"{'▰' * filled}{'▱' * (5 - filled)} {float(pos):.0%}"


def _market_context_lines(conn, b) -> list[str]:
    """Deterministic reviewer-domain market context (Phase C 2026-07-30;
    panel expansion 2026-08-07): vol regime + StockRover-style per-symbol
    panels (valuation w/ sector+index percentiles, profitability, growth,
    financial strength, dividends, returns & risk, warnings) from the
    newest cached Schwab documents, computed at render time. No LLM
    touches this section; nothing here enters analyst context, basket
    construction, or any assertion table (invariant 5). Best-effort: dark
    or absent data renders a one-line note, never fails the render; each
    panel degrades independently (absence, not repair)."""
    from config.loader import CONFIG

    if not CONFIG.get("schwab", {}).get("enabled"):
        return []
    lines = ["", "## Market context (reviewer-domain — quarantined)", "",
             "*Deterministic render-time snapshot from cached Schwab"
             " documents (`quarantined_from_analyst`). Never analyst input,"
             " never a decision input — invariant 5. No LLM touches this"
             " section.*", ""]
    try:
        from datetime import date as _date
        from decimal import Decimal
        from statistics import median

        from analytics.market_context import (
            market_warnings, max_drawdown, payout_ratio, period_return,
            valuation_snapshot,
        )
        from analytics.regime import level_percentile, vol_regime
        from analytics.valuation import percentile_rank
        from analytics.volatility import annualized_volatility
        from data.adapters import schwab

        cfg = CONFIG["schwab"]
        symbols = sorted((b.theme.research_config or {}).get("symbols", [])
                         if b.theme else [])
        quotes, quotes_tag = schwab.latest_quotes(conn, kind="theme")
        tape, _ = schwab.latest_quotes(conn, kind="tape")
        funds, funds_tag = schwab.latest_fundamentals(conn)

        def safe(fn, default):
            try:
                return fn()
            except Exception:  # noqa: BLE001 — panels degrade independently
                return default

        # Cross-sectional context: full-universe fundamentals batch (may be
        # older than the theme prefetch batch) + GICS sector membership.
        universe, universe_tag = safe(
            lambda: schwab.universe_fundamentals(conn), ({}, None))
        sector_of: dict[str, str] = safe(
            lambda: dict(conn.execute(
                "SELECT ticker, gics_sector FROM sp500_constituents"
            ).fetchall()), {})

        vix_symbol = cfg.get("vix_history", {}).get("symbol", "$VIX")
        vix = tape.get(vix_symbol)
        if vix and vix.last is not None:
            history = [c.close for c in schwab.latest_index_history(conn, vix_symbol)]
            pct = level_percentile(vix.last, history)
            bands = [(label, Decimal(str(v)))
                     for label, v in cfg.get("regime_bands", [])]
            regime = vol_regime(pct, bands)
            pct_txt = (f", {float(pct) * 100:.0f}th percentile vs 10y"
                       if pct is not None else "")
            lines.append(f"Vol regime: VIX {float(vix.last):.2f}{pct_txt}"
                         + (f" — **{regime}**." if regime else "."))
            lines.append("")

        fmt = lambda v, spec="{:.1f}": "—" if v is None else spec.format(float(v))  # noqa: E731
        pos_pes = {s: f.pe for s, f in universe.items()
                   if f.pe is not None and f.pe > 0}
        dtc_threshold = Decimal(str(cfg.get("crowding", {})
                                    .get("days_to_cover_flag", "8")))
        crosscheck_tol = Decimal(str(
            cfg.get("fundamentals", {})
            .get("pe_crosscheck_tolerance_pct", "5"))) / 100

        present = [s for s in symbols
                   if funds.get(s) is not None or quotes.get(s) is not None]
        if not present:
            lines.append("*No cached Schwab fundamentals/quotes for the"
                         " theme symbols yet.*")
            return lines

        snaps, sector_pctile, index_pctile, payouts = {}, {}, {}, {}
        for symbol in present:
            f, q = funds.get(symbol), quotes.get(symbol)
            sector = sector_of.get(symbol)
            sector_pes = [pe for s, pe in pos_pes.items()
                          if sector and sector_of.get(s) == sector]
            snaps[symbol] = valuation_snapshot(
                symbol,
                vendor_pe=f.pe if f else None, pb=f.pb if f else None,
                peg=f.peg if f else None, div_yield=f.div_yield if f else None,
                market_cap=f.market_cap if f else None,
                beta=f.beta if f else None,
                short_int_pct_float=f.short_int_pct_float if f else None,
                short_int_days_to_cover=f.short_int_days_to_cover if f else None,
                last=q.last if q else None,
                lo52=(q.low_52wk if q and q.low_52wk is not None
                      else (f.low_52wk if f else None)),
                hi52=(q.high_52wk if q and q.high_52wk is not None
                      else (f.high_52wk if f else None)),
                eps_ttm=f.eps_ttm if f else None,
                peer_pes=sector_pes,
                crosscheck_tolerance=crosscheck_tol,
            )
            if f and f.pe is not None and f.pe > 0 and len(pos_pes) >= 20:
                index_pctile[symbol] = percentile_rank(f.pe, list(pos_pes.values()))
                if len(sector_pes) >= 5:
                    sector_pctile[symbol] = snaps[symbol].pe_pctile_vs_peers
            payouts[symbol] = payout_ratio(
                f.div_amount if f else None, f.eps_ttm if f else None)

        def panel(title: str, header: list[str],
                  row: "Callable[[str], list[str] | None]") -> None:
            rows = [(s, row(s)) for s in present]
            rows = [(s, r) for s, r in rows if r and any(c != "—" for c in r)]
            if not rows:
                return
            lines.append(f"### {title}")
            lines.append("")
            lines.append("| Symbol | " + " | ".join(header) + " |")
            lines.append("|---" * (len(header) + 1) + "|")
            for s, r in rows:
                lines.append(f"| {s} | " + " | ".join(r) + " |")
            lines.append("")

        def _cap(v):
            return "—" if v is None else f"${float(v) / 1e9:,.1f}B"

        panel("Snapshot", ["Last", "Day %", "Mkt cap", "Beta", "52wk range"],
              lambda s: [
                  fmt(quotes[s].last, "{:.2f}") if quotes.get(s) else "—",
                  fmt(quotes[s].net_pct_change, "{:+.2f}") if quotes.get(s) else "—",
                  _cap(snaps[s].market_cap),
                  fmt(snaps[s].beta, "{:.2f}"),
                  _gauge(snaps[s].range_position_52wk)])

        panel("Valuation",
              ["P/E", "vs sector", "vs S&P 500", "P/S", "P/CF", "P/B", "PEG"],
              lambda s: [
                  fmt(snaps[s].pe),
                  _gauge(sector_pctile.get(s)),
                  _gauge(index_pctile.get(s)),
                  fmt(funds[s].ps, "{:.2f}") if funds.get(s) else "—",
                  fmt(funds[s].pcf, "{:.1f}") if funds.get(s) else "—",
                  fmt(snaps[s].pb, "{:.2f}"),
                  fmt(snaps[s].peg, "{:.2f}")])

        panel("Profitability",
              ["Gross %", "Oper %", "Net %", "ROE %", "ROA %", "ROI %"],
              lambda s: [
                  fmt(funds[s].gross_margin_ttm), fmt(funds[s].operating_margin_ttm),
                  fmt(funds[s].net_margin_ttm), fmt(funds[s].roe),
                  fmt(funds[s].roa), fmt(funds[s].roi)]
              if funds.get(s) else None)

        panel("Growth (vendor-reported %)",
              ["Rev Δ TTM", "Rev Δ 1Y", "EPS Δ TTM"],
              lambda s: [
                  fmt(funds[s].rev_change_ttm), fmt(funds[s].rev_change_year),
                  fmt(funds[s].eps_change_ttm)]
              if funds.get(s) else None)

        panel("Financial strength",
              ["D/E", "LT D/E", "Current", "Quick", "BV/share"],
              lambda s: [
                  fmt(funds[s].total_debt_to_equity / 100 if funds[s].total_debt_to_equity is not None else None, "{:.2f}x"),
                  fmt(funds[s].lt_debt_to_equity / 100 if funds[s].lt_debt_to_equity is not None else None, "{:.2f}x"),
                  fmt(funds[s].current_ratio, "{:.2f}"),
                  fmt(funds[s].quick_ratio, "{:.2f}"),
                  fmt(funds[s].book_value_per_share, "${:.2f}")]
              if funds.get(s) else None)

        panel("Dividends",
              ["Fwd $/yr", "Yield %", "Freq", "Payout %", "Next ex-div", "Pays"],
              lambda s: [
                  fmt(funds[s].div_amount, "${:.2f}"),
                  fmt(funds[s].div_yield, "{:.2f}"),
                  str(funds[s].div_freq) if funds[s].div_freq else "—",
                  fmt(payouts.get(s), "{:.0f}"),
                  funds[s].next_div_ex_date or "—",
                  funds[s].next_div_pay_date or "—"]
              if funds.get(s) and funds[s].div_amount is not None else None)

        # Returns & risk from the nightly Schwab singles window (~130 days,
        # split-adjusted only — footnoted below). Benchmark row = median of
        # the full singles universe over the same windows.
        closes, closes_tag = safe(
            lambda: schwab.latest_single_closes(conn, cutoff=_date.today()),
            ({}, None))
        if closes:
            # Windows bounded by the nightly singles history (~90 sessions
            # of the 130-calendar-day window) — no column that can't fill.
            windows = [("1M", 21), ("3M", 63)]

            def _ret_row(series):
                out = [period_return(series, n) for _, n in windows]
                out.append(max_drawdown(series))
                out.append(Decimal(str(annualized_volatility(series)))
                           if len(series) >= 30 else None)
                return out

            def row_for(s):
                series = closes.get(s)
                if not series:
                    return None
                return [fmt(v, "{:+.1%}") if i < 3 else fmt(v, "{:.2f}")
                        for i, v in enumerate(_ret_row(series))]

            panel("Returns & risk (≤3M window, split-adjusted)",
                  ["1M", "3M", "Max DD", "Vol (ann)"], row_for)
            med = []
            for _, n in windows:
                vals = sorted(v for v in
                              (period_return(c, n)
                               for c in closes.values()) if v is not None)
                med.append(median(vals) if vals else None)
            if any(v is not None for v in med) and len(closes) >= 100:
                lines.insert(
                    len(lines) - 1,
                    f"| *S&P 500 median* | {fmt(med[0], '{:+.1%}')}"
                    f" | {fmt(med[1], '{:+.1%}')} | — | — |")

        warn_lines = []
        for s in present:
            f = funds.get(s)
            flags = market_warnings(
                rev_change_ttm=f.rev_change_ttm if f else None,
                rev_change_year=f.rev_change_year if f else None,
                eps_change_ttm=f.eps_change_ttm if f else None,
                eps_ttm=f.eps_ttm if f else None,
                payout=payouts.get(s),
                pe_pctile_vs_peers=sector_pctile.get(s),
                range_position_52wk=snaps[s].range_position_52wk,
                days_to_cover=snaps[s].short_int_days_to_cover,
                days_to_cover_threshold=dtc_threshold,
            )
            warn_lines += [f"- **{s}** — {flag}" for flag in flags]
        warn_lines += [f"- ⚠ {snap.symbol} — {snap.pe_crosscheck_note}"
                       for snap in snaps.values() if snap.pe_crosscheck_note]
        if warn_lines:
            lines.append("### Warnings (deterministic rules)")
            lines.append("")
            lines += warn_lines
            lines.append("")

        lines.append(
            f"*Fundamentals batch {funds_tag or 'n/a'}"
            + (f" (percentiles vs universe batch {universe_tag})"
               if universe_tag and universe_tag != funds_tag else "")
            + f"; quotes batch {quotes_tag or 'n/a'}. Valuation/margin trend"
            " tables unlock as weekly snapshots accumulate.*")
        return lines
    except Exception as e:  # noqa: BLE001 — context must never fail the render
        return lines + [f"*Market context unavailable:"
                        f" {type(e).__name__}: {str(e)[:120]}*"]


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

    # 4b — market context (reviewer-domain, deterministic, quarantined)
    lines.extend(_market_context_lines(conn, b))

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
    _publish_html(content, thesis, week_key)
    return [local, vault]


def _publish_html(content: str, thesis: Thesis, week_key: str) -> None:
    """HTML companion + ntfy tap-to-open link (read-along delivery pattern,
    2026-08-07). Best-effort by contract: the vault markdown above is the
    artifact of record — an HTML or push failure never fails the dive."""
    try:
        from ops import alerts
        from reporting import deepdive_html

        url = deepdive_html.publish(
            content, f"{week_key}-{thesis.strategy_tag}",
            title=f"Deep dive {week_key} — {thesis.strategy_tag}")
        if url:
            alerts.notify(
                title=f"Deep dive ready — {thesis.strategy_tag}",
                message=(f"{week_key} · {thesis.status.value}\n"
                         f"Tap to read, or open {url}"),
                tags="bar_chart", click=url)
    except Exception as e:  # noqa: BLE001 — companion must never fail the dive
        print(f"[deepdive-html:skipped {type(e).__name__}: {e}]", file=sys.stderr)
