"""Daily report → vault + reporting/output. Deterministic render of stored

rows; adds no claims. This file is the desk's face in the daily briefing
(Agents_DailyBriefing reads it from the vault), so it carries every section
the consolidated pipeline surfaces: theme queue, thesis decisions pending,
per-thesis monitoring (one line per ACTIVE thesis EVERY day — a missing row
renders loudly as MONITOR GAP), desk activity, incidents, pipeline health."""

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
    lines += _theme_queue_section(conn, session)
    lines += _thesis_decisions_section(conn)
    lines += _monitoring_section(conn, exp_id, session)
    lines += _fills_section(conn, exp_id, session)

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


# --------------------------------------------------------------------------- #
# Consolidated-pipeline sections (spec 2026-07-22)
# --------------------------------------------------------------------------- #

def _theme_queue_section(conn, session: date) -> list[str]:
    holder = conn.execute(
        "SELECT slug FROM themes WHERE accepted_on = %s", (session,)
    ).fetchone()
    candidates = conn.execute(
        "SELECT slug, title, source, proposed_on, rolled_forward_count, description"
        " FROM themes WHERE status = 'CANDIDATE' ORDER BY proposed_on, id"
    ).fetchall()
    lines = ["", "## Theme queue", ""]
    lines.append(
        f"Today's acceptance slot: "
        + (f"**USED** by `{holder[0]}`" if holder
           else "**OPEN** (one accepted theme per day, all sources)")
    )
    # Each candidate renders in full — the description is the decision
    # document, so the briefing must carry enough to accept or reject from.
    for slug, title, source, proposed_on, rolled, description in candidates:
        age = max(0, (session - proposed_on).days)
        rolled_note = f", rolled forward x{rolled}" if rolled else ""
        lines += ["", f"### `{slug}` ({source.lower()}, {age}d old{rolled_note})",
                  "", f"**{title}**"]
        if description:
            lines += ["", description.strip()]
    if not candidates:
        # The queue can be legitimately empty at render time: this report
        # writes before the intake phase, and the scout's mandatory daily
        # proposal lands in the NEXT morning's briefing.
        lines.append("- (queue empty — tonight's scout proposal lands in"
                     " tomorrow's briefing)")
    deferred = conn.execute(
        "SELECT count(*), min(resurface_on) FROM themes WHERE status = 'DEFERRED'"
    ).fetchone()
    if deferred and deferred[0]:
        lines.append(
            f"- _{deferred[0]} undecided theme(s) deferred — resurface {deferred[1]}_"
        )
    lines.append("")
    lines.append("Decide: `uv run python -m ops.approve theme accept|reject <slug>`"
                 " — undecided candidates defer to Monday on their own;"
                 " reject closes the thread for good.")
    return lines


def _thesis_decisions_section(conn) -> list[str]:
    reviewed = conn.execute(
        "SELECT id, strategy_tag, title FROM theses WHERE status = 'REVIEWED'"
        " ORDER BY id"
    ).fetchall()
    if not reviewed:
        return []
    lines = ["", "## ⏳ Thesis decisions pending", ""]
    for tid, tag, title in reviewed:
        lines.append(
            f"- thesis #{tid} [`{tag}`]: {title} — deep dive in"
            f" `reports/deepdive/`; decide with"
            f" `uv run python -m ops.approve thesis accept|reject {tid}`"
        )
    return lines


def _monitoring_section(conn, exp_id: int, session: date) -> list[str]:
    active = conn.execute(
        "SELECT t.id, t.strategy_tag, s.verdict, s.summary, s.post_trade"
        " FROM theses t LEFT JOIN thesis_daily_status s"
        " ON s.thesis_id = t.id AND s.session_date = %s"
        " WHERE t.experiment_id = %s AND t.status = 'ACTIVE' ORDER BY t.id",
        (session, exp_id),
    ).fetchall()
    lines = ["", "## Monitoring", ""]
    if not active:
        lines.append("_No thesis under monitoring._")
        return lines
    # Reversals first and loud, then WATCH, then explicit no-change.
    rank = {"REVERSAL_SUSPECTED": 0, "WATCH": 1, "NO_CHANGE": 2, None: 3}
    for tid, tag, verdict, summary, post_trade in sorted(
        active, key=lambda r: (rank.get(r[2], 3), r[0])
    ):
        stage = "post-trade" if post_trade else "pre-trade"
        if verdict is None:
            lines.append(
                f"- 🟥 **MONITOR GAP** `{tag}` (#{tid}): no status row for"
                f" {session} — monitoring must never be silent; investigate"
            )
        elif verdict == "REVERSAL_SUSPECTED":
            lines.append(
                f"- ⚠ **REVERSAL SIGNAL** `{tag}` (#{tid}, {stage}): {summary}"
            )
        elif verdict == "WATCH":
            lines.append(f"- 🟡 WATCH `{tag}` (#{tid}, {stage}): {summary}")
        else:
            lines.append(f"- `{tag}` (#{tid}, {stage}): no change")
    return lines


def _fills_section(conn, exp_id: int, session: date) -> list[str]:
    fills = conn.execute(
        "SELECT t.symbol, t.side, t.qty, t.fill_price, t.strategy_tag, t.exit_type"
        " FROM trades t JOIN orders o ON t.order_id = o.id"
        " WHERE o.experiment_id = %s AND t.executed_at::date = %s ORDER BY t.id",
        (exp_id, session),
    ).fetchall()
    if not fills:
        return []
    lines = ["", "## Fills this session", ""]
    for symbol, side, qty, price, tag, exit_type in fills:
        exit_note = f" ({exit_type})" if exit_type else ""
        lines.append(f"- {side} {qty} {symbol} @ {price} [`{tag}`]{exit_note}")
    return lines
