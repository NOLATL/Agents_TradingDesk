"""Momentum-scan report → vault + reporting/output. Deterministic render of
computed scan values (analytics/scan.py); the only non-deterministic section
is the guarded narrator lead (agents/roles/scan_narrator.py), which is
best-effort and clearly labeled.

OPERATOR-ONLY, quarantined from the analyst (invariant 5): this report is a
hypothesis vector for the human. Nothing here may flow into LLM analyst/scout
context, theme_symbols, basket construction, or the engine.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from analytics.scan import PocketScore, breadth
from analytics.scan_internals import PocketInternals
from config.loader import SLICE

QUARANTINE_BANNER = (
    "*Operator-only technical scan (`quarantined_from_analyst`). Never an"
    " input to basket construction, theme_symbols, or any LLM analyst/scout"
    " context — invariant 5.*"
)


@dataclass(frozen=True)
class TapeRow:
    symbol: str                    # as quoted ($SPX, /ES, EUR/USD)
    label: str | None
    last: Decimal | None
    net_pct: Decimal | None        # day change, percent points (e.g. -0.85)


@dataclass(frozen=True)
class MoverLine:
    symbol: str
    description: str | None
    net_pct: Decimal | None


@dataclass(frozen=True)
class MacroTape:
    rows: tuple[TapeRow, ...]
    vix_level: Decimal | None
    vix_percentile: Decimal | None          # 0..1 vs ~10y history
    vix_regime: str | None                  # calm/normal/elevated/stressed
    risk_score: Decimal | None              # -1..+1 composite
    risk_present: int = 0
    risk_total: int = 0
    movers: tuple[MoverLine, ...] = ()


@dataclass(frozen=True)
class PocketRow:
    ticker: str
    name: str
    kind: str                      # sector | industry | theme-etf
    score: PocketScore
    delta: int | None              # rank move vs prior scan; None = new entrant


@dataclass(frozen=True)
class ThemeRow:
    symbol: str
    gics_sector: str | None        # None = not in the S&P 500 snapshot
    gics_sub_industry: str | None
    score: PocketScore


@dataclass(frozen=True)
class ScanReportData:
    session: date
    horizons: tuple[int, ...]
    pockets: tuple[PocketRow, ...]                                # ranked, best first
    unscored: tuple[str, ...] = ()                                # insufficient history
    theme_sections: tuple[tuple[str, tuple[ThemeRow, ...]], ...] = ()
    hot_pocket_members: tuple[tuple[str, tuple[str, ...]], ...] = ()
    gaps: tuple[tuple[str, str], ...] = ()                        # (symbol, reason)
    prior_session: date | None = None
    fetched: int = 0
    cache_hits: int = 0
    ingest_skipped: int = 0        # skipped/failed fetches served from cache
    # Phase A (three-vendor architecture): single-name internals + referee
    internals: tuple[PocketInternals, ...] = ()
    index_internals: PocketInternals | None = None
    internals_note: str | None = None      # ingest summary / degradation note
    referee_notes: tuple[str, ...] = ()
    macro: MacroTape | None = None
    schwab_dark_note: str | None = None    # "Schwab dark since <date>: <why>"
    snapshot_refreshed: datetime | None = None
    narration: str | None = None
    narration_note: str | None = None


HORIZON_LABELS = {5: "5d", 21: "21d", 63: "63d"}


def _hlabel(h: int) -> str:
    return HORIZON_LABELS.get(h, f"{h}s")


def _pct(v: Decimal | None) -> str:
    return "—" if v is None else f"{float(v) * 100:+.1f}%"


def _z(v: Decimal | None) -> str:
    return "—" if v is None else f"{float(v):+.2f}"


def _delta(d: int | None) -> str:
    if d is None:
        return "NEW"
    if d > 0:
        return f"▲{d}"
    if d < 0:
        return f"▼{-d}"
    return "·"


# --------------------------------------------------------------------------- #
# Narrator inputs — the context text IS the guard corpus, so everything the
# model may restate must be in this string (narrative_guard build rule).
# --------------------------------------------------------------------------- #

def narrator_inputs(data: ScanReportData) -> tuple[str, set[float], set[str]]:
    """(context_text, numeric values, allowed tickers) for narrate_scan."""
    values: set[float] = set()
    symbols: set[str] = set()

    def add(score: PocketScore) -> None:
        for v in (*score.returns, *score.zscores, score.composite):
            if v is not None:
                values.update({float(v), float(v) * 100})

    lines = [f"MOMENTUM SCAN {data.session} — pockets ranked by composite momentum"
             f" z-score (horizons: {', '.join(_hlabel(h) for h in data.horizons)});"
             f" prior scan {data.prior_session or 'n/a'}.", "", "POCKET LEADERBOARD:"]
    for rank, p in enumerate(data.pockets, 1):
        symbols.add(p.ticker)
        add(p.score)
        if p.delta is not None:
            values.add(float(abs(p.delta)))
        rets = " ".join(
            f"{_hlabel(h)} {_pct(r)}" for h, r in zip(data.horizons, p.score.returns)
        )
        lines.append(
            f"{rank}. {p.name} ({p.ticker}, {p.kind}) composite"
            f" {_z(p.score.composite)}, {rets},"
            f" rank move {_delta(p.delta)}"
        )
    for slug, rows in data.theme_sections:
        lines += ["", f"DESK THEME {slug} (symbols scored in their own"
                      " single-name cross-section):"]
        for r in rows:
            symbols.add(r.symbol)
            add(r.score)
            pocket = r.gics_sub_industry or r.gics_sector or "not in S&P 500"
            rets = " ".join(
                f"{_hlabel(h)} {_pct(v)}" for h, v in zip(data.horizons, r.score.returns)
            )
            lines.append(f"- {r.symbol} [{pocket}] composite"
                         f" {_z(r.score.composite)}, {rets}")
        if rows:
            pos, known = breadth({r.symbol: r.score.returns[0] for r in rows})
            values.update({float(pos), float(known)})
            lines.append(f"breadth: {pos}/{known} positive {_hlabel(data.horizons[0])}")
    if data.macro:
        m = data.macro
        lines += ["", "MACRO TAPE:"]
        for r in m.rows:
            symbols.update({r.symbol, r.symbol.lstrip("$/").replace("/", " ")})
            for v in (r.last, r.net_pct):
                if v is not None:
                    values.add(float(v))
            day = "n/a" if r.net_pct is None else f"{float(r.net_pct):+.2f}%"
            lines.append(f"- {r.symbol} ({r.label or 'tape'}): last"
                         f" {r.last if r.last is not None else 'n/a'}, day {day}")
        if m.vix_level is not None:
            values.add(float(m.vix_level))
            if m.vix_percentile is not None:
                values.update({float(m.vix_percentile), float(m.vix_percentile) * 100})
            lines.append(
                f"- VIX {m.vix_level}"
                + (f", percentile {float(m.vix_percentile) * 100:.0f}"
                   if m.vix_percentile is not None else "")
                + (f", regime {m.vix_regime}" if m.vix_regime else ""))
        if m.risk_score is not None:
            values.update({float(m.risk_score),
                           float(m.risk_present), float(m.risk_total)})
            lines.append(f"- risk-on/off composite {float(m.risk_score):+.2f}"
                         f" ({m.risk_present}/{m.risk_total} components)")
        for mv in m.movers:
            symbols.add(mv.symbol)
            if mv.net_pct is not None:
                values.add(float(mv.net_pct))
            lines.append(f"- mover {mv.symbol}"
                         + (f" {float(mv.net_pct):+.1f}%" if mv.net_pct is not None else ""))

    rows = ([data.index_internals] if data.index_internals else []) + list(data.internals)
    if rows:
        lines += ["", "POCKET INTERNALS (single-name members, full-index z):"]
        for it in rows:
            for v in (it.median_member_composite, it.pct_above_avg, it.dispersion):
                if v is not None:
                    values.update({float(v), float(v) * 100})
            values.update({float(it.members_scored), float(it.members_total),
                           float(it.breadth_pos), float(it.breadth_known),
                           float(it.new_highs), float(it.new_lows)})
            pct = ("n/a" if it.pct_above_avg is None
                   else f"{float(it.pct_above_avg) * 100:.0f}%")
            disp = ("n/a" if it.dispersion is None
                    else f"{float(it.dispersion) * 100:.1f}%")
            lines.append(
                f"- {it.pocket}: {it.members_scored}/{it.members_total} scored,"
                f" median composite {_z(it.median_member_composite)},"
                f" breadth {it.breadth_pos}/{it.breadth_known} positive"
                f" {_hlabel(data.horizons[0])}, {pct} above 50-session average,"
                f" {it.new_highs} new highs / {it.new_lows} new lows,"
                f" dispersion {disp}")
    if data.gaps:
        lines += ["", "DATA GAPS (excluded): "
                  + ", ".join(f"{s} ({reason})" for s, reason in data.gaps)]
    return "\n".join(lines), values, symbols


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #

def render_scan(data: ScanReportData) -> str:
    h = data.horizons
    lines = [f"# Momentum scan — {data.session}", "", QUARANTINE_BANNER, ""]

    if data.narration:
        lines += ["## Summary", "",
                  "*Narrated by the local model over the computed values below;"
                  " mechanically guarded, judgment not fact.*", "",
                  data.narration, ""]
    elif data.narration_note:
        lines += [f"*Narration unavailable: {data.narration_note}*", ""]

    if data.macro or data.schwab_dark_note:
        lines += ["## Macro tape", ""]
        if data.schwab_dark_note:
            lines += [f"*{data.schwab_dark_note}*", ""]
        elif data.macro:
            m = data.macro
            if m.rows:
                lines += ["| Series | Last | Day |", "|---|---|---|"]
                for r in m.rows:
                    day = "—" if r.net_pct is None else f"{float(r.net_pct):+.2f}%"
                    last = "—" if r.last is None else f"{float(r.last):,.2f}"
                    label = f" {r.label}" if r.label else ""
                    lines.append(f"| {r.symbol}{label} | {last} | {day} |")
                lines.append("")
            if m.vix_level is not None:
                pct = ("" if m.vix_percentile is None
                       else f", {float(m.vix_percentile) * 100:.0f}th percentile vs 10y")
                regime = f" — **{m.vix_regime}** regime" if m.vix_regime else ""
                lines.append(f"VIX {float(m.vix_level):.2f}{pct}{regime}.")
            if m.risk_score is not None:
                lines.append(
                    f"Risk-on/off composite: {float(m.risk_score):+.2f}"
                    f" ({m.risk_present}/{m.risk_total} components; -1 risk-off,"
                    " +1 risk-on).")
            if m.movers:
                lines += ["", "Movers ($SPX):"]
                for mv in m.movers:
                    pct = "" if mv.net_pct is None else f" {float(mv.net_pct):+.1f}%"
                    desc = f" — {mv.description}" if mv.description else ""
                    lines.append(f"- {mv.symbol}{pct}{desc}")
        lines.append("")

    lines += ["## Pocket leaderboard", "",
              f"| # | Δ | Pocket | Kind | ETF | Composite z | {' | '.join(_hlabel(x) for x in h)} |",
              "|---|---|---|---|---|---|" + "---|" * len(h)]
    for rank, p in enumerate(data.pockets, 1):
        rets = " | ".join(_pct(r) for r in p.score.returns)
        lines.append(f"| {rank} | {_delta(p.delta)} | {p.name} | {p.kind}"
                     f" | {p.ticker} | {_z(p.score.composite)} | {rets} |")

    movers = [(i + 1, p) for i, p in enumerate(data.pockets)
              if p.delta is None or abs(p.delta) >= 5]
    if movers and data.prior_session:
        lines += ["", "## Movers vs prior scan"
                      f" ({data.prior_session})", ""]
        for rank, p in movers:
            move = "new to the ranking" if p.delta is None else \
                f"{'up' if p.delta > 0 else 'down'} {abs(p.delta)} to #{rank}"
            lines.append(f"- **{p.name}** ({p.ticker}) {move},"
                         f" composite {_z(p.score.composite)}")

    sectors = [p for p in data.pockets if p.kind == "sector"]
    if sectors:
        lines += ["", "## Sector snapshot", ""]
        for p in sectors:
            lines.append(f"- {p.name} ({p.ticker}): {_z(p.score.composite)}"
                         f" ({_hlabel(h[1] if len(h) > 1 else h[0])}"
                         f" {_pct(p.score.returns[1] if len(h) > 1 else p.score.returns[0])})")

    if data.theme_sections:
        lines += ["", "## Desk themes → GICS pockets", "",
                  "*Composite z-scored within the theme-symbol cross-section,"
                  " not the ETF cross-section.*"]
        for slug, rows in data.theme_sections:
            lines += ["", f"### {slug}", "",
                      f"| Symbol | GICS pocket | Composite z | {' | '.join(_hlabel(x) for x in h)} |",
                      "|---|---|---|" + "---|" * len(h)]
            for r in rows:
                pocket = (f"{r.gics_sector} / {r.gics_sub_industry}"
                          if r.gics_sub_industry else (r.gics_sector or "not in S&P 500"))
                rets = " | ".join(_pct(v) for v in r.score.returns)
                lines.append(f"| {r.symbol} | {pocket}"
                             f" | {_z(r.score.composite)} | {rets} |")
            if rows:
                parts = []
                for i, hz in enumerate(h[:2]):
                    pos, known = breadth({r.symbol: r.score.returns[i] for r in rows})
                    parts.append(f"{pos}/{known} positive {_hlabel(hz)}")
                lines.append("")
                lines.append(f"Breadth: {', '.join(parts)}.")

    if data.internals or data.internals_note:
        lines += ["", "## Pocket internals", "",
                  "*Single-name members per pocket (Schwab daily candles —"
                  " split-adjusted only, dividends not reflected in member"
                  " momentum); member composites z-scored across the full"
                  " S&P 500 cross-section.*"]
        if data.internals_note:
            lines += ["", f"*{data.internals_note}*"]
        if data.internals:
            lines += ["",
                      "| Pocket | Members | Median z | Breadth 5d | >50d avg"
                      " | New hi/lo | Disp 21d |",
                      "|---|---|---|---|---|---|---|"]
            rows = list(data.internals)
            if data.index_internals:
                rows = [data.index_internals] + rows
            for it in rows:
                pct = ("—" if it.pct_above_avg is None
                       else f"{float(it.pct_above_avg) * 100:.0f}%")
                disp = ("—" if it.dispersion is None
                        else f"{float(it.dispersion) * 100:.1f}%")
                lines.append(
                    f"| {it.pocket} | {it.members_scored}/{it.members_total}"
                    f" | {_z(it.median_member_composite)}"
                    f" | {it.breadth_pos}/{it.breadth_known} | {pct}"
                    f" | {it.new_highs}/{it.new_lows} | {disp} |")

    if data.referee_notes:
        lines += ["", "## Cross-vendor referee", ""]
        lines += [f"- {n}" for n in data.referee_notes]

    if data.hot_pocket_members:
        lines += ["", "## Inside the hot pockets", "",
                  "*S&P 500 members of the top pockets' configured GICS"
                  " sub-industries (weekly snapshot).*"]
        for name, members in data.hot_pocket_members:
            lines += ["", f"- **{name}**: {', '.join(members)}"]

    lines += ["", "## Data gaps & scan health", ""]
    if data.gaps:
        lines += [f"- {sym}: {reason}" for sym, reason in data.gaps]
    if data.unscored:
        lines.append(f"- insufficient history (excluded from cross-section):"
                     f" {', '.join(data.unscored)}")
    if not data.gaps and not data.unscored:
        lines.append("- all pockets scored")
    snap = (data.snapshot_refreshed.date().isoformat()
            if data.snapshot_refreshed else "never")
    skipped = (f", {data.ingest_skipped} fetch(es) skipped/failed but served"
               " from cached documents" if data.ingest_skipped else "")
    lines.append(f"- fetched {data.fetched}, cache-hit {data.cache_hits}{skipped};"
                 f" constituents snapshot {snap};"
                 f" prior scan session {data.prior_session or 'n/a'}")
    return "\n".join(lines) + "\n"


def write_scan_report(content: str, session: date) -> Path:
    """Dual-write: repo output + Obsidian vault (the morning briefing reads
    the vault). Deterministic overwrite — idempotent re-runs rewrite the
    same files."""
    out = Path(__file__).parent / "output" / "momentum-scan" / f"{session}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    vault = Path(SLICE["vault_dir"]) / "reports" / "momentum-scan" / f"{session}.md"
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(content)
    return vault
