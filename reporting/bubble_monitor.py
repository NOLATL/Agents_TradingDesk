"""AI-bubble exit monitor rendering — terminal dashboard (ANSI) and vault
markdown. Pure: report data in, text out. Operator-only (invariant 5)."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from analytics.bubble_monitor import (
    GREEN,
    NO_DATA,
    RED,
    YELLOW,
    LinkStatus,
    ReplayPoint,
    ScoreResult,
    StageResult,
)
from config.loader import SLICE
from data.adapters.bubble_feeds import VAST_ATTRIBUTION, VastPrice

QUARANTINE_BANNER = (
    "*Operator-only personal de-risking dashboard (`quarantined_from_analyst`)."
    " Never an input to basket construction, evidence, or any LLM role —"
    " invariant 5. The stage is a pre-committed rule's output; the decision"
    " is yours.*"
)

STAGE_TEXT = {
    0: "WATCH",
    1: "TRIM",
    2: "DEFEND",
    3: "OUT",
}
ACTION_TEXT = {
    0: "Hold. Keep the review cadence.",
    1: "De-risk tranche 1: cut AI-concentrated exposure first.",
    2: "De-risk tranche 2.",
    3: "Final tranche, down to your floor allocation.",
}

_ICON = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴", NO_DATA: "⚪"}
_ANSI = {GREEN: "\033[32m", YELLOW: "\033[33m", RED: "\033[31m", NO_DATA: "\033[90m"}
_BOLD, _RESET = "\033[1m", "\033[0m"
_BLOCKS = "▁▂▃▄▅▆▇█"


@dataclass(frozen=True)
class HardRed:
    key: str
    description: str
    note: str
    raised_on: date


@dataclass(frozen=True)
class BubbleReport:
    session: date
    links: list[LinkStatus]
    result: StageResult                                 # raw rule output today
    hard_reds: list[HardRed]
    previous_stage: int | None
    gaps: list[str] = field(default_factory=list)       # dark vendors, failed fetches
    stage: int = 0                                      # held (debounced): sets the action
    score: ScoreResult = ScoreResult(None, None, Decimal(0), {})
    history: list[ReplayPoint] = field(default_factory=list)
    escalation: str = ""
    tranche_pct: dict | None = None
    vast: list[VastPrice] = field(default_factory=list)
    vast_as_of: str | None = None


def sparkline(values: list[Decimal | None], width: int = 24) -> str:
    """Last values downsampled to `width` blocks; gaps render as spaces."""
    if not values:
        return ""
    if len(values) > width:
        step = len(values) / width
        values = [values[int(i * step)] for i in range(width - 1)] + [values[-1]]
    real = [v for v in values if v is not None]
    if not real:
        return " " * len(values)
    lo, hi = min(real), max(real)
    span = (hi - lo) or Decimal(1)
    return "".join(" " if v is None else _BLOCKS[int((v - lo) / span * 7)] for v in values)


def _value_at(history: list[ReplayPoint], days_back: int) -> ReplayPoint | None:
    if not history:
        return None
    target = history[-1].day - timedelta(days=days_back)
    prior = [p for p in history if p.day <= target]
    return prior[-1] if prior else None


def score_delta(history: list[ReplayPoint], days_back: int) -> Decimal | None:
    then = _value_at(history, days_back)
    if then is None or then.score is None or history[-1].score is None:
        return None
    return history[-1].score - then.score


def _delta(x: Decimal | None) -> str:
    return "  n/a" if x is None else f"{float(x):+5.0f}"


def _trend_word(x: Decimal | None) -> str:
    if x is None:
        return "no history"
    if x >= 5:
        return "rising"
    return "falling" if x <= -5 else "flat"


def _action(r: BubbleReport) -> str:
    text = ACTION_TEXT[r.stage]
    if r.stage == 0:
        return text
    pct = (r.tranche_pct or {}).get(r.stage)
    return text + (f" Size: {pct}% of the position." if pct else
                   " Size: unset (bubble_monitor.tranche_pct).")


def _human_cell(l: LinkStatus) -> str:
    if l.human_stale:
        return f"STALE ({l.human_recorded_on})"
    if l.human:
        return f"{l.human} ({l.human_recorded_on})"
    return "—"


def _stage_line(r: BubbleReport) -> str:
    change = ""
    if r.previous_stage is not None and r.previous_stage != r.stage:
        arrow = "▲" if r.stage > r.previous_stage else "▼"
        change = f" {arrow} from {r.previous_stage}"
    return f"STAGE {r.stage}{change} — {STAGE_TEXT[r.stage]}"


def _pending(r: BubbleReport) -> str | None:
    """Raw rule differs from the held stage: say so, it is the early warning."""
    if r.result.stage == r.stage:
        return None
    return (f"Rule reads stage {r.result.stage} today; not in effect until it holds"
            f" ({'; '.join(r.result.reasons)}).")


def _score_line(r: BubbleReport) -> str:
    s = r.score
    if s.score is None:
        return "Risk score: not available"
    return (f"Risk score {s.score:.0f}/100 ({s.band}), {_trend_word(score_delta(r.history, 30))}"
            f" over 1 mo; {s.coverage * 100:.0f}% of the chain scored")


def render_markdown(r: BubbleReport) -> str:
    lines = [
        "---", f"date: {r.session}", "type: bubble-monitor",
        f"stage: {r.stage}",
        f"raw_stage: {r.result.stage}",
        f"score: {'' if r.score.score is None else f'{r.score.score:.0f}'}",
        f"band: {r.score.band or ''}",
        f"red_links: [{', '.join(r.result.red_links)}]", "---", "",
        f"# AI-bubble exit monitor — {r.session}", "", QUARANTINE_BANNER, "",
        f"## {_stage_line(r)}", "", _score_line(r) + ".", "",
        f"**Action:** {_action(r)}", "",
    ]
    pending = _pending(r)
    if pending:
        lines += [f"**Pending:** {pending}", ""]
    lines += [r.escalation, ""]
    lines += [f"- {reason}" for reason in r.result.reasons]
    lines.append(f"- market confirmation: {'yes' if r.result.market_confirmed else 'no'}")
    lines.append("")
    if r.hard_reds:
        lines += ["## Active hard reds", ""]
        lines += [f"- 🔴 **{h.key}** — {h.description} (raised {h.raised_on}): {h.note}"
                  for h in r.hard_reds]
        lines.append("")
    lines += ["## Links", "", "| Link | Status | Score | Auto | Human | Note |",
              "|---|---|---|---|---|---|"]
    for l in r.links:
        sc = r.score.links.get(l.link)
        lines.append(f"| {l.link} {l.name} | {_ICON[l.status]} {l.status} "
                     f"| {'—' if sc is None else f'{sc:.0f}'} | {l.auto} "
                     f"| {_human_cell(l)} | {l.human_note or ''} |")
    lines += ["", "## Automated signals", "",
              "| Link | Signal | Status | Severity | Reading | As of |",
              "|---|---|---|---|---|---|"]
    for l in r.links:
        for s in l.signals:
            sev = "—" if s.severity is None else f"{s.severity:.0f}"
            lines.append(f"| {s.link} | {s.label} | {_ICON[s.status]} {s.status} | {sev} "
                         f"| {s.detail} | {s.as_of or '—'} |")
    if r.vast:
        lines += ["", f"## GPU spot prices ({VAST_ATTRIBUTION})", "",
                  "| GPU | Median $/hr | Offers | 30d ago |", "|---|---|---|---|"]
        lines += [f"| {v.name} | {v.median:.2f} | {v.available} "
                  f"| {'—' if v.median_30d_ago is None else f'{v.median_30d_ago:.2f}'} |"
                  for v in r.vast]
    unscored = [l.link for l in r.links if l.status == NO_DATA]
    stale = [l.link for l in r.links if l.human_stale]
    if unscored or stale or r.gaps:
        lines += ["", "## ⚠ Gaps", ""]
        if unscored:
            lines.append(f"- **UNSCORED links: {', '.join(unscored)}** — no automated signal and no "
                         "fresh human score; score them (`ops.bubble_monitor score`).")
        if stale:
            lines.append(f"- STALE human scores: {', '.join(stale)}")
        lines += [f"- {g}" for g in r.gaps]
    lines.append("")
    return "\n".join(lines)


def _gauge(score: Decimal, width: int = 40) -> str:
    filled = int(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def render_terminal(r: BubbleReport, *, color: bool = True) -> str:
    def c(text: str, status: str | None = None, bold: bool = False) -> str:
        if not color:
            return text
        pre = (_ANSI[status] if status else "") + (_BOLD if bold else "")
        return f"{pre}{text}{_RESET}" if pre else text

    stage_status = {0: GREEN, 1: YELLOW, 2: RED, 3: RED}[r.stage]
    score = r.score
    band_status = {"LOW": GREEN, "ELEVATED": YELLOW}.get(score.band, RED)
    out = [c(f"AI-BUBBLE RISK  {r.session}", bold=True), ""]
    if score.score is not None:
        out.append(c(f"{score.score:5.0f}/100 ", band_status, bold=True)
                   + c(_gauge(score.score), band_status) + c(f" {score.band}", band_status, bold=True))
        spark = sparkline([p.score for p in r.history], 40)
        out.append(f"  6 mo    {spark}")
        out.append(f"  change  1w {_delta(score_delta(r.history, 7))}   "
                   f"1m {_delta(score_delta(r.history, 30))}   "
                   f"3m {_delta(score_delta(r.history, 91))}   "
                   f"coverage {score.coverage * 100:.0f}%")
    out += ["", c(_stage_line(r), stage_status, bold=True), f"  ACTION  {_action(r)}"]
    pending = _pending(r)
    if pending:
        out.append(c(f"  PENDING {pending}", YELLOW, bold=True))
    else:
        out.append(f"  WHY     {'; '.join(r.result.reasons)}")
    out.append(f"  NEXT    {r.escalation}")
    for h in r.hard_reds:
        out.append(c(f"  ■ HARD RED {h.key}: {h.note} ({h.raised_on})", RED, bold=True))
    out.append("")
    prior_month = _value_at(r.history, 30)
    for l in r.links:
        sc = score.links.get(l.link)
        then = prior_month.links.get(l.link) if prior_month else None
        link_delta = None if sc is None or then is None else sc - then
        spark = sparkline([p.links.get(l.link) for p in r.history], 16)
        head = (f"{l.link} {l.name:<25} {'--' if sc is None else f'{sc:3.0f}':>3} "
                f"{_delta(link_delta)} {spark:<16} ")
        out.append(c(head, None, bold=True) + c(l.status, l.status, bold=True)
                   + (f"  human {_human_cell(l)}" if l.human or l.human_stale else ""))
        if l.human_note and l.human:
            out.append(c(f"     “{l.human_note}”", NO_DATA))
        for s in l.signals:
            trend = sparkline([p.signals.get(s.id) for p in r.history], 16)
            sev = "--" if s.severity is None else f"{s.severity:3.0f}"
            out.append("  " + c(f"{sev:>3}", s.status) + f" {s.label:<40} {trend:<16} {s.detail}")
    if r.vast:
        out += ["", c("GPU spot, " + VAST_ATTRIBUTION, bold=True)]
        for v in r.vast:
            then = "" if v.median_30d_ago is None else f"   30d ago ${v.median_30d_ago:.2f}"
            out.append(f"  {v.name:<10} ${v.median:.2f}/hr  {v.available:>3} offers{then}")
    unscored = [l.link for l in r.links if l.status == NO_DATA]
    if unscored or r.gaps:
        out.append("")
        if unscored:
            out.append(c(f"UNSCORED: {', '.join(unscored)} — "
                         "uv run python -m ops.bubble_monitor score <LINK> <GREEN|YELLOW|RED> \"note\"",
                         YELLOW, bold=True))
        out += [c(f"GAP: {g}", YELLOW) for g in r.gaps]
    return "\n".join(out)


def write_report(content: str, session: date) -> Path:
    """Dual-write like the momentum scan: repo output + vault. Deterministic
    overwrite — idempotent re-runs rewrite the same files."""
    out = Path(__file__).parent / "output" / "bubble-monitor" / f"{session}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    vault = Path(SLICE["vault_dir"]) / "reports" / "bubble-monitor" / f"{session}.md"
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(content)
    return vault


def stage_change_message(r: BubbleReport) -> tuple[str, str, str] | None:
    """(title, body, priority) for ntfy when the stage moved; else None."""
    if r.previous_stage is None or r.previous_stage == r.stage:
        return None
    up = r.stage > r.previous_stage
    title = f"AI-bubble monitor: stage {r.previous_stage} → {r.stage}"
    body = f"{STAGE_TEXT[r.stage]}: {_action(r)} " + "; ".join(r.result.reasons)
    return title, body, ("high" if up else "default")
