"""Weekly proposal cycle (systemd timer Sat 09:00 ET) + event-driven mode.

Phases (cycle_log claims make each idempotent; resume from last phase):
  research  — the four Stage 1 roles over each ACTIVE theme (skipped in
              event mode)
  proposal  — reviewer-ACTIVATED theses → sized PROPOSED orders (packet
              cached to documents; the human sees what the desk saw)
  notify    — HALT FOR APPROVAL: alert; the cycle never auto-approves.
              Fills happen at the next session open AFTER the human runs
              ops/approve.py — the daily monitor's genesis replay picks
              them up. There is no path around the gate.
  report    — weekly report render

    uv run python -m ops.weekly_cycle
    uv run python -m ops.weekly_cycle --trigger causal-break:<milestone_id>
"""

import sys
from datetime import date, datetime, timezone

from agents import run_slice
from config.loader import CONFIG
from data import repository as repo
from data.db import connect
from ops.alerts import loud, notify
from ops.proposal_builder import build_proposals, cache_packet
from reporting.weekly import render_weekly
from schemas.models import CycleLog, Thesis

STAGE3 = CONFIG["stage3"]


def claim(conn, cycle_type: str, period_key: str, trigger: str) -> CycleLog | None:
    row = conn.execute(
        "INSERT INTO cycle_log (cycle_type, period_key, trigger, status, started_at)"
        " VALUES (%s, %s, %s, 'RUNNING', %s)"
        " ON CONFLICT (cycle_type, period_key) DO NOTHING RETURNING id",
        (cycle_type, period_key, trigger, datetime.now(timezone.utc)),
    ).fetchone()
    if row is not None:
        conn.commit()
        return repo.fetch_one(conn, CycleLog, "id = %s", (row[0],))
    existing = repo.fetch_one(
        conn, CycleLog, "cycle_type = %s AND period_key = %s", (cycle_type, period_key)
    )
    if existing.status == "COMPLETE":
        return None
    conn.execute(
        "UPDATE cycle_log SET status = 'RUNNING', error = NULL WHERE id = %s",
        (existing.id,),
    )
    conn.commit()
    return existing


def mark(conn, cycle: CycleLog, phase: str, status: str = "RUNNING", error=None):
    conn.execute(
        "UPDATE cycle_log SET phase = %s, status = %s, error = %s,"
        " finished_at = CASE WHEN %s IN ('COMPLETE','FAILED') THEN now() ELSE NULL END"
        " WHERE id = %s", (phase, status, error, status, cycle.id),
    )
    conn.commit()


def active_themes() -> dict[str, list[str]]:
    return {
        name: cfg["symbols"]
        for name, cfg in sorted(STAGE3["themes"].items())
        if cfg.get("active")
    }


def research_pass(conn, cycle: CycleLog) -> None:
    """One research pass per theme per week; resume skips completed themes

    (phase records 'research:<theme>')."""
    done = (cycle.phase or "").split(",") if cycle.phase else []
    for theme, symbols in active_themes().items():
        marker = f"research:{theme}"
        if marker in done:
            continue
        print(f"research pass: {theme} ({symbols})")
        run_slice.run(symbols=symbols, theme=theme)
        done.append(marker)
        mark(conn, cycle, ",".join(done))


def proposal_pass(conn) -> int:
    """Sized proposals for every ACTIVE thesis without orders."""
    exp_id = conn.execute(
        "SELECT id FROM experiments WHERE name = %s", (STAGE3["live_experiment"],)
    ).fetchone()[0]
    today = datetime.now(timezone.utc).date()
    n = 0
    for thesis in repo.fetch(
        conn, Thesis, "experiment_id = %s AND status = 'ACTIVE' ORDER BY id", (exp_id,)
    ):
        packet_doc_id = None
        packet = _latest_packet_path()
        if packet:
            packet_doc_id = cache_packet(conn, packet, datetime.now(timezone.utc))
        orders = build_proposals(conn, thesis, today, packet_doc_id)
        n += len(orders)
    return n


def _latest_packet_path() -> str | None:
    from pathlib import Path

    out = Path(__file__).parent.parent / "reporting" / "output"
    packets = sorted(out.glob("decision-packet-*.md"))
    return str(packets[-1]) if packets else None


@loud("weekly_cycle")
def main(trigger: str = "timer") -> None:
    now = datetime.now(timezone.utc)
    if trigger.startswith("causal-break:"):
        cycle_type, period_key = "event", trigger
    else:
        cycle_type, period_key = "weekly", f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"

    with connect() as conn:
        cycle = claim(conn, cycle_type, period_key, trigger)
        if cycle is None:
            print(f"{cycle_type} cycle {period_key} already COMPLETE — no-op")
            return
        try:
            if cycle_type == "weekly":
                research_pass(conn, cycle)

            n_proposed = proposal_pass(conn)
            mark(conn, cycle, "proposal")

            pending = conn.execute(
                "SELECT count(*) FROM orders WHERE status = 'PROPOSED'"
            ).fetchone()[0]
            if pending:
                notify(
                    "Approval needed",
                    f"{pending} order(s) awaiting your decision "
                    f"({n_proposed} new this cycle). The desk does nothing until you run:\n"
                    f"uv run python -m ops.approve",
                    priority="high", tags="hourglass_flowing_sand",
                )
            mark(conn, cycle, "notify")

            render_weekly(conn, period_key)
            mark(conn, cycle, "report", status="COMPLETE")
            print(f"{cycle_type} cycle {period_key} COMPLETE: {n_proposed} proposed, "
                  f"{pending} pending approval")
        except Exception as e:
            mark(conn, cycle, cycle.phase or "unknown", status="FAILED", error=str(e)[:1500])
            raise


if __name__ == "__main__":
    arg = sys.argv[1].removeprefix("--trigger=") if len(sys.argv) > 1 else "timer"
    main(arg)
