"""Weekly report → vault + reporting/output. Calibration snapshot, attribution,

theses, approvals, lessons, audit trail — all from stored rows."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from analytics.engine.metrics import calibration_curve, mean_brier
from config.loader import CONFIG, SLICE
from schemas.enums import ForecastOutcome


def render_weekly(conn, week_key: str) -> Path:
    exp_name = CONFIG["stage3"]["live_experiment"]
    exp_id = conn.execute(
        "SELECT id FROM experiments WHERE name = %s", (exp_name,)
    ).fetchone()[0]
    now = datetime.now(timezone.utc)

    lines = [f"# Desk weekly — {week_key}", ""]

    theses = conn.execute(
        "SELECT id, version, status, title, strategy_tag FROM theses"
        " WHERE experiment_id = %s ORDER BY id DESC LIMIT 10", (exp_id,),
    ).fetchall()
    lines += ["## Theses", ""]
    for tid, v, status, title, tag in theses:
        lines.append(f"- #{tid} v{v} [{status}] {title} (`{tag}`)")

    pending = conn.execute(
        "SELECT id, side, proposed_qty, symbol, exit_type FROM orders"
        " WHERE status = 'PROPOSED' ORDER BY id"
    ).fetchall()
    lines += ["", f"## Awaiting approval ({len(pending)})", ""]
    for oid, side, qty, sym, exit_type in pending:
        extra = f" EXIT:{exit_type}" if exit_type else ""
        lines.append(f"- order #{oid}: {side} {qty} {sym}{extra}")
    if pending:
        lines.append("\nRun `uv run python -m ops.approve`.")

    # Calibration snapshot (resolved forecasts only; UNRESOLVED never scores)
    pairs = [
        (float(p), ForecastOutcome(o))
        for p, o in conn.execute(
            "SELECT f.probability, f.outcome FROM forecasts f"
            " JOIN theses t ON f.thesis_id = t.id"
            " WHERE t.experiment_id = %s AND f.outcome IS NOT NULL", (exp_id,),
        )
    ]
    lines += ["", "## Calibration", ""]
    mb = mean_brier(pairs)
    resolved = [p for p in pairs if p[1] is not ForecastOutcome.UNRESOLVED]
    lines.append(f"- resolved forecasts: {len(resolved)} (of {len(pairs)} closed)")
    lines.append(f"- running mean Brier: {mb:.4f}" if mb is not None else "- running mean Brier: n/a")
    for b in calibration_curve(pairs):
        lines.append(
            f"  - {b['bucket']}: predicted {b['predicted_mean']:.2f}, "
            f"observed {b['observed_frequency']:.2f} (n={b['count']})"
        )

    attribution = conn.execute(
        "SELECT t.strategy_tag, sum(l.realized_pnl) FROM tax_lots l"
        " JOIN trades t ON l.closing_trade_id = t.id"
        " JOIN orders o ON t.order_id = o.id WHERE o.experiment_id = %s"
        " AND l.realized_pnl IS NOT NULL GROUP BY 1", (exp_id,),
    ).fetchall()
    if attribution:
        lines += ["", "## Realized attribution by strategy_tag", ""]
        lines += [f"- `{tag}`: {pnl}" for tag, pnl in attribution]

    lessons = conn.execute(
        "SELECT dimension, left(lesson_text, 100) FROM lessons"
        " WHERE created_at >= %s ORDER BY id DESC", (now - timedelta(days=8),),
    ).fetchall()
    if lessons:
        lines += ["", "## Lessons this week", ""]
        lines += [f"- [{d}] {t}" for d, t in lessons]

    runs = conn.execute(
        "SELECT role, count(*), count(*) FILTER (WHERE success) FROM agent_runs"
        " WHERE started_at >= %s GROUP BY role", (now - timedelta(days=8),),
    ).fetchall()
    lines += ["", "## Agent runs (7d)", ""]
    lines += [f"- {r}: {n} ({ok} ok)" for r, n, ok in runs] or ["- none"]

    content = "\n".join(lines) + "\n"
    out = Path(__file__).parent / "output" / "weekly" / f"{week_key}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    vault = Path(SLICE["vault_dir"]) / "reports" / "weekly" / f"{week_key}.md"
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(content)
    return vault
