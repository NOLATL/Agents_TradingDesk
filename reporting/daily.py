"""Daily report → vault + reporting/output. Deterministic render of stored

rows; adds no claims."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from config.loader import CONFIG, SLICE


def render_daily(conn, session: date, summary) -> Path:
    exp_name = CONFIG["stage3"]["live_experiment"]
    exp_id = conn.execute(
        "SELECT id FROM experiments WHERE name = %s", (exp_name,)
    ).fetchone()[0]

    lines = [f"# Desk daily — {session}", ""]
    if summary.dry_run:
        lines.append(f"*Dry-run (pre-genesis {CONFIG['stage3']['genesis_date']}): "
                     "guards + ingestion only; no live ledger yet.*")
    nav_rows = conn.execute(
        "SELECT nav_date, nav, cash, positions_value, contains_forced_resolution"
        " FROM daily_nav WHERE experiment_id = %s ORDER BY nav_date DESC LIMIT 1",
        (exp_id,),
    ).fetchall()
    if nav_rows:
        d, nav, cash, pv, tainted = nav_rows[0]
        taint = "  ⚠ TAINTED (forced resolution in series)" if tainted else ""
        lines += ["", f"## NAV  {nav}  (cash {cash} + positions {pv}){taint}", ""]
        genesis_navs = {
            r[0]: r[1] for r in conn.execute(
                "SELECT benchmark_name, nav FROM benchmarks_nav"
                " WHERE experiment_id = %s AND nav_date = %s", (exp_id, d),
            )
        }
        if genesis_navs:
            lines.append("| Series | NAV |")
            lines.append("|---|---|")
            lines.append(f"| **desk** | {nav} |")
            for name in sorted(genesis_navs):
                lines.append(f"| {name} | {genesis_navs[name]} |")
    positions = conn.execute(
        "SELECT DISTINCT ON (symbol) symbol, qty, avg_cost, market_value, frozen"
        " FROM positions WHERE experiment_id = %s ORDER BY symbol, as_of DESC",
        (exp_id,),
    ).fetchall()
    live_positions = [p for p in positions if p[1] and p[1] != 0]
    if live_positions:
        lines += ["", "## Positions", "", "| Symbol | Qty | Avg cost | Value | Frozen |", "|---|---|---|---|---|"]
        for sym, qty, avg, value, frozen in live_positions:
            lines.append(f"| {sym} | {qty} | {avg} | {value} | {'YES' if frozen else ''} |")
    pending = conn.execute(
        "SELECT count(*) FROM orders WHERE status = 'PROPOSED'"
    ).fetchone()[0]
    if pending:
        lines += ["", f"## ⏳ {pending} order(s) awaiting approval — `uv run python -m ops.approve`"]
    incidents = conn.execute(
        "SELECT incident_class, symbol, left(description, 90) FROM data_incidents"
        " WHERE status = 'OPEN' ORDER BY id DESC LIMIT 10"
    ).fetchall()
    if incidents:
        lines += ["", "## Open incidents", ""]
        lines += [f"- **{c}** {s or ''}: {d}" for c, s, d in incidents]
    milestones = conn.execute(
        "SELECT m.description, m.status, m.status_changed_at FROM milestones m"
        " JOIN theses t ON m.thesis_id = t.id WHERE m.status != 'PENDING'"
        " AND m.status_changed_at >= %s::date - INTERVAL '2 days'", (session,),
    ).fetchall()
    if milestones:
        lines += ["", "## Milestone events", ""]
        lines += [f"- [{s}] {d[:120]}" for d, s, _ in milestones]
    health = conn.execute(
        "SELECT cycle_type, period_key, status, phase FROM cycle_log"
        " ORDER BY id DESC LIMIT 5"
    ).fetchall()
    lines += ["", "## Pipeline", ""]
    lines += [f"- {t} {k}: {s} ({p})" for t, k, s, p in health]
    if getattr(summary, "pinned_symbols", ()):
        lines += ["", f"⚠ pinned (restatement pending human): {', '.join(summary.pinned_symbols)}"]

    content = "\n".join(lines) + "\n"
    out = Path(__file__).parent / "output" / "daily" / f"{session}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    vault = Path(SLICE["vault_dir"]) / "reports" / "daily" / f"{session}.md"
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text(content)
    return vault
