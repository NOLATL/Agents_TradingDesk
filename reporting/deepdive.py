"""Friday-night deep-dive report — one individual artifact per accepted theme
(consolidation spec: per-theme, never batched). Thesis-centric: the tradeable
hypothesis, the chain of events that must hold, the falsifiable milestones
monitoring will check for reversal, and how to accept/reject. Referenced from
the daily briefing; not duplicated inside it.
"""

import json
from pathlib import Path

from config.loader import SLICE
from data import repository as repo
from schemas.models import Forecast, Milestone, Theme, Thesis

OUTPUT_DIR = Path(__file__).parent / "output" / "deepdive"


def render(conn, thesis: Thesis, week_key: str, packet_path: str | None) -> str:
    theme = (
        repo.fetch_one(conn, Theme, "id = %s", (thesis.theme_id,))
        if thesis.theme_id else None
    )
    milestones = repo.fetch(
        conn, Milestone, "thesis_id = %s ORDER BY id", (thesis.id,)
    )
    forecasts = repo.fetch(
        conn, Forecast, "thesis_id = %s ORDER BY id", (thesis.id,)
    )
    try:
        basket = json.loads(thesis.exposure_basket_approach)
    except (TypeError, ValueError):
        basket = []

    rejected = thesis.status.value == "REJECTED"
    lines = [
        f"# Deep dive {week_key}: {thesis.title}",
        "",
        f"- thesis: #{thesis.id} v{thesis.version} ({thesis.status.value})",
        f"- theme: {thesis.strategy_tag}"
        + (f" (source: {theme.source.value.lower()})" if theme else ""),
    ]
    if rejected:
        lines += ["", "> ⚠ **REVIEWER REJECTED this draft** — no decision"
                  " needed. The theme stays ACCEPTED and the dive retries next"
                  " Friday; close the thread with `theme reject` if the idea"
                  " itself is dead. Reviewer challenges:"]
        row = conn.execute(
            "SELECT value FROM assertions WHERE value->>'kind' = 'reviewer_audit'"
            " AND (value->>'thesis_id')::int = %s ORDER BY id DESC LIMIT 1",
            (thesis.id,),
        ).fetchone()
        for challenge in (row[0].get("challenges", []) if row else []):
            lines.append(f"> - {challenge}")
    if packet_path:
        lines.append(f"- evidence packet: {packet_path}")
    lines += ["", "## Tradeable hypothesis — chain of events", "",
              thesis.causal_chain, ""]

    lines.append("## Milestones (reversal is checked against these)")
    lines.append("")
    for m in milestones:
        lines.append(f"- [{m.status.value}] {m.description}")
        lines.append(f"  - falsified if: {m.falsification_criterion}")
    if not milestones:
        lines.append("- (none — monitoring has nothing falsifiable to check)")
    lines.append("")

    lines.append("## Forecasts")
    lines.append("")
    for f in forecasts:
        lines.append(
            f"- p={f.probability:.2f} by {f.horizon_end}: {f.statement}"
        )
    lines.append("")

    if basket:
        lines.append("## Proposed basket (measured exposure only)")
        lines.append("")
        for row in basket:
            lines.append(
                f"- #{row.get('rank')} {row.get('symbol')}: "
                f"{row.get('justification', '')[:200]}"
            )
        lines.append("")

    lines += [
        "## Valuation check", "", thesis.valuation_check, "",
        "## Do-nothing case", "", thesis.do_nothing_case or "(none recorded)", "",
        "## Exit criteria", "",
    ]
    for exit_type, criterion in sorted((thesis.exit_criteria or {}).items()):
        lines.append(f"- {exit_type}: {criterion}")
    if rejected:
        lines += [
            "",
            "---",
            "No decision needed — this draft never reached you. To stop the"
            f" weekly retries: `uv run python -m ops.approve theme reject"
            f" {thesis.strategy_tag} --rationale \"...\"`",
            "",
        ]
    else:
        lines += [
            "",
            "---",
            "Your decision (this thesis does nothing until you decide):",
            "```",
            f"uv run python -m ops.approve thesis accept {thesis.id}",
            f"uv run python -m ops.approve thesis reject {thesis.id} --rationale \"...\"",
            "```",
            "",
        ]
    return "\n".join(lines)


def write(conn, thesis: Thesis, week_key: str,
          packet_path: str | None = None) -> list[Path]:
    content = render(conn, thesis, week_key, packet_path)
    name = f"{week_key}-{thesis.strategy_tag}.md"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    local = OUTPUT_DIR / name
    local.write_text(content)
    vault = Path(SLICE["vault_dir"]) / "reports" / "deepdive" / name
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(content)
    return [local, vault]
