"""Slippage calibration report — READ-ONLY during EXP-001 (DECISIONS.md:

tier changes mid-run would rewrite genesis-replay history; an actual
recalibration happens only at an experiment boundary).

Compares each live fill's modeled slippage against the realized bar: how far
the raw open moved intraday (a context measure of adverse-selection room) and
the next session's open gap.

    uv run python -m ops.slippage_report
"""

from decimal import Decimal
from pathlib import Path

from config.loader import CONFIG, SLICE
from data.db import connect

D = Decimal


def report() -> str:
    exp_name = CONFIG["stage3"]["live_experiment"]
    with connect() as conn:
        rows = conn.execute(
            "SELECT t.symbol, t.side, t.qty, t.fill_price, t.slippage_applied,"
            " t.executed_at::date AS session, pb.open, pb.high, pb.low, pb.close"
            " FROM trades t JOIN orders o ON t.order_id = o.id"
            " JOIN experiments e ON o.experiment_id = e.id"
            " JOIN price_bars pb ON pb.symbol = t.symbol"
            "   AND pb.session_date = t.executed_at::date"
            " WHERE e.name = %s ORDER BY t.id", (exp_name,),
        ).fetchall()

    lines = ["# Slippage calibration report (read-only during EXP-001)", ""]
    if not rows:
        lines.append("No live fills yet.")
    else:
        lines += [
            "| Session | Symbol | Side | Modeled slip | Slip bps | Intraday range bps | Open→close bps |",
            "|---|---|---|---|---|---|---|",
        ]
        slips, ranges = [], []
        for sym, side, qty, fill, slip, session, o, h, l, c in rows:
            slip_bps = slip / o * 10000 if o else D(0)
            range_bps = (h - l) / o * 10000 if o else D(0)
            oc_bps = (c - o) / o * 10000 if o else D(0)
            slips.append(slip_bps)
            ranges.append(range_bps)
            lines.append(
                f"| {session} | {sym} | {side} | {slip} | {slip_bps:.1f} |"
                f" {range_bps:.1f} | {oc_bps:+.1f} |"
            )
        lines += [
            "",
            f"- fills: {len(rows)}; mean modeled slip {sum(slips)/len(slips):.1f} bps;"
            f" mean intraday range {sum(ranges)/len(ranges):.1f} bps",
            "- Interpretation: modeled slip should sit well inside the typical range;",
            "  a persistent mismatch is evidence for retiering AT THE NEXT EXPERIMENT",
            "  BOUNDARY, not mid-run.",
        ]
    content = "\n".join(lines) + "\n"
    out = Path(SLICE["vault_dir"]) / "reports" / "slippage-calibration.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    return content


if __name__ == "__main__":
    print(report())
