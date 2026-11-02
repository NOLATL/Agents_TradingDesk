"""AI-bubble exit monitor rendering — terminal (ANSI) and vault markdown.
Pure: report data in, text out. Operator-only (invariant 5)."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from analytics.bubble_monitor import GREEN, NO_DATA, RED, YELLOW, LinkStatus, StageResult
from config.loader import SLICE

QUARANTINE_BANNER = (
    "*Operator-only personal de-risking dashboard (`quarantined_from_analyst`)."
    " Never an input to basket construction, evidence, or any LLM role —"
    " invariant 5. The stage is a pre-committed rule's output; the decision"
    " is yours.*"
)

STAGE_TEXT = {
    0: "WATCH — hold; keep the review cadence",
    1: "TRIM — de-risk tranche 1 (cut AI-concentrated exposure first)",
    2: "DEFEND — de-risk tranche 2",
    3: "OUT — final tranche to your floor allocation",
}

_ICON = {GREEN: "🟢", YELLOW: "🟡", RED: "🔴", NO_DATA: "⚪"}
_ANSI = {GREEN: "\033[32m", YELLOW: "\033[33m", RED: "\033[31m", NO_DATA: "\033[90m"}
_BOLD, _RESET, _DIM = "\033[1m", "\033[0m", "\033[2m"


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
    result: StageResult
    hard_reds: list[HardRed]
    previous_stage: int | None
    gaps: list[str] = field(default_factory=list)       # dark vendors, failed fetches


def _human_cell(l: LinkStatus) -> str:
    if l.human_stale:
        return f"STALE (scored {l.human_recorded_on})"
    if l.human:
        return f"{l.human} ({l.human_recorded_on})"
    return "—"


def _stage_line(r: BubbleReport) -> str:
    change = ""
    if r.previous_stage is not None and r.previous_stage != r.result.stage:
        arrow = "▲" if r.result.stage > r.previous_stage else "▼"
        change = f" {arrow} from {r.previous_stage}"
    return f"STAGE {r.result.stage}{change} — {STAGE_TEXT[r.result.stage]}"


def render_markdown(r: BubbleReport) -> str:
    lines = [
        "---", f"date: {r.session}", "type: bubble-monitor",
        f"stage: {r.result.stage}",
        f"red_links: [{', '.join(r.result.red_links)}]", "---", "",
        f"# AI-bubble exit monitor — {r.session}", "", QUARANTINE_BANNER, "",
        f"## {_stage_line(r)}", "",
    ]
    lines += [f"- {reason}" for reason in r.result.reasons]
    lines.append(f"- market confirmation: {'yes' if r.result.market_confirmed else 'no'}")
    lines.append("")
    if r.hard_reds:
        lines += ["## Active hard reds", ""]
        lines += [f"- 🔴 **{h.key}** — {h.description} (raised {h.raised_on}): {h.note}"
                  for h in r.hard_reds]
        lines.append("")
    lines += ["## Links", "", "| Link | Status | Auto | Human | Note |", "|---|---|---|---|---|"]
    for l in r.links:
        lines.append(f"| {l.link} {l.name} | {_ICON[l.status]} {l.status} | {l.auto} "
                     f"| {_human_cell(l)} | {l.human_note or ''} |")
    lines += ["", "## Automated signals", "",
              "| Link | Signal | Status | Reading | As of |", "|---|---|---|---|---|"]
    for l in r.links:
        for s in l.signals:
            lines.append(f"| {s.link} | {s.label} | {_ICON[s.status]} {s.status} "
                         f"| {s.detail} | {s.as_of or '—'} |")
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


def render_terminal(r: BubbleReport, *, color: bool = True) -> str:
    def c(text: str, status: str | None = None, bold: bool = False) -> str:
        if not color:
            return text
        pre = (_ANSI[status] if status else "") + (_BOLD if bold else "")
        return f"{pre}{text}{_RESET}" if pre else text

    stage_status = {0: GREEN, 1: YELLOW, 2: RED, 3: RED}[r.result.stage]
    out = [c(f"AI-BUBBLE EXIT MONITOR  {r.session}", bold=True),
           c(_stage_line(r), stage_status, bold=True)]
    out += [f"  · {reason}" for reason in r.result.reasons]
    out.append(f"  · market confirmation: {'YES' if r.result.market_confirmed else 'no'}")
    for h in r.hard_reds:
        out.append(c(f"  ■ HARD RED {h.key}: {h.note} ({h.raised_on})", RED, bold=True))
    out.append("")
    for l in r.links:
        human = _human_cell(l)
        out.append(c(f"{l.link} {l.name:<26} {l.status:<8}", l.status, bold=True)
                   + c(f" auto {l.auto:<8} human {human}", None))
        if l.human_note and l.human:
            out.append(c(f"     “{l.human_note}”", NO_DATA))
        for s in l.signals:
            out.append("   " + c(f"{s.status:<8}", s.status) + f" {s.label:<44} {s.detail}")
    unscored = [l.link for l in r.links if l.status == NO_DATA]
    if unscored or r.gaps:
        out.append("")
        if unscored:
            out.append(c(f"UNSCORED: {', '.join(unscored)} — "
                         "python -m ops.bubble_monitor score <LINK> <GREEN|YELLOW|RED> \"note\"",
                         YELLOW, bold=True))
        out += [c(f"gap: {g}", NO_DATA) for g in r.gaps]
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
    if r.previous_stage is None or r.previous_stage == r.result.stage:
        return None
    up = r.result.stage > r.previous_stage
    title = f"AI-bubble monitor: stage {r.previous_stage} → {r.result.stage}"
    body = f"{STAGE_TEXT[r.result.stage]}. " + "; ".join(r.result.reasons)
    return title, body, ("high" if up else "default")
