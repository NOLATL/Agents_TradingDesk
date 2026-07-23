"""Friday-night deep-dive cycle (systemd timer Fri 23:45 ET) + event mode.

Phases (cycle_log claims make each idempotent; resume from last phase):
  research  — one INDIVIDUAL deep dive per theme accepted via the intake
              queue that has no surviving thesis yet (never batched). Each
              dive ends REVIEWED at most — the reviewer is a quality
              pre-filter; only the human's `thesis accept` activates. A
              reviewer-REJECTED draft leaves the theme ACCEPTED, so it
              retries next Friday unless the user closes it.
  proposal  — safety net: sized PROPOSED orders for any ACTIVE thesis still
              missing them (normally proposals are built at accept time by
              ops/approve.py, from the thesis's own cached packet).
  notify    — HALT FOR DECISIONS: alert; the cycle never auto-approves.
              There is no path around the gate.
  report    — per-theme deep-dive reports + the weekly ops summary

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
from reporting import deepdive
from reporting.weekly import render_weekly
from schemas.enums import ThesisStatus
from schemas.models import CycleLog, Theme, Thesis

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
    # Keep the in-memory row in sync: the FAILED handler re-marks with
    # cycle.phase, and a stale value would wipe research_pass's per-theme
    # resume markers (forcing duplicate dives on the rerun).
    cycle.phase = phase


def themes_awaiting_research(conn) -> list[Theme]:
    """ACCEPTED intake themes with no surviving thesis (REVIEWED/ACTIVE/
    INVALIDATED/CLOSED). Reviewer-REJECTED drafts don't count — the theme
    retries next Friday until a thesis survives review or the user closes it."""
    return repo.fetch(
        conn, Theme,
        "status = 'ACCEPTED' AND NOT EXISTS ("
        " SELECT 1 FROM theses t WHERE t.theme_id = themes.id"
        " AND t.status IN ('REVIEWED', 'ACTIVE', 'INVALIDATED', 'CLOSED'))"
        " ORDER BY accepted_on, id",
    )


def research_pass(conn, cycle: CycleLog) -> tuple[list[Thesis], list[Thesis]]:
    """One INDIVIDUAL deep dive per due theme; resume skips completed themes
    (phase records 'research:<theme>'). Returns (survivors, rejected)."""
    done = (cycle.phase or "").split(",") if cycle.phase else []
    survivors: list[Thesis] = []
    rejected: list[Thesis] = []
    for theme in themes_awaiting_research(conn):
        marker = f"research:{theme.slug}"
        if marker in done:
            continue
        rc = theme.research_config or {}
        print(f"deep dive: {theme.slug} ({rc.get('symbols')})")
        thesis, packet_paths = run_slice.run(
            symbols=rc.get("symbols"), theme=theme.slug, theme_id=theme.id,
            emphasis_terms=rc.get("emphasis_terms"),
            fred_series=rc.get("fred_series"),
        )
        # Cache the packet NOW, keyed by theme in its filename — `thesis
        # accept` later attaches exactly this evidence to the orders.
        if packet_paths:
            cache_packet(conn, str(packet_paths[0]), datetime.now(timezone.utc))
            conn.commit()
        # The artifact is written on BOTH verdicts: a reviewer-REJECTED dive
        # must still show the human what was tried and why it failed —
        # silence would read as "nothing happened Friday".
        (survivors if thesis.status is ThesisStatus.REVIEWED else rejected).append(thesis)
        deepdive.write(conn, thesis, cycle.period_key,
                       str(packet_paths[0]) if packet_paths else None)
        done.append(marker)
        mark(conn, cycle, ",".join(done))
    return survivors, rejected


def _rerender_daily(conn) -> None:
    """Refresh the newest daily note after Friday's dives so Saturday's
    briefing (which reads the daily note, rendered 45 min before this cycle)
    shows the new REVIEWED theses / rejected dives. Pure re-render of stored
    rows; adds no claims. Best-effort: a render failure must not fail the
    cycle after the research work is committed."""
    from types import SimpleNamespace

    from ops.daily_monitor import latest_closed_session
    from reporting.daily import render_daily

    session = latest_closed_session(datetime.now(timezone.utc))
    if session is None:
        return
    try:
        render_daily(conn, session,
                     SimpleNamespace(dry_run=False, pinned_symbols=()))
    except Exception as e:  # noqa: BLE001
        print(f"daily re-render after weekly cycle failed: {e}", file=sys.stderr)


def proposal_pass(conn) -> int:
    """Safety net: sized proposals for any ACTIVE thesis without orders,
    using that thesis's OWN packet (never the globally-latest one)."""
    from ops.approve import _thesis_packet_doc_id

    exp_id = conn.execute(
        "SELECT id FROM experiments WHERE name = %s", (STAGE3["live_experiment"],)
    ).fetchone()[0]
    today = datetime.now(timezone.utc).date()
    n = 0
    for thesis in repo.fetch(
        conn, Thesis, "experiment_id = %s AND status = 'ACTIVE' ORDER BY id", (exp_id,)
    ):
        orders = build_proposals(
            conn, thesis, today, _thesis_packet_doc_id(conn, thesis)
        )
        n += len(orders)
    return n


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
            survivors, rejected = [], []
            if cycle_type == "weekly":
                survivors, rejected = research_pass(conn, cycle)

            n_proposed = proposal_pass(conn)
            mark(conn, cycle, "proposal")

            pending = conn.execute(
                "SELECT count(*) FROM orders WHERE status = 'PROPOSED'"
            ).fetchone()[0]
            reviewed = conn.execute(
                "SELECT count(*) FROM theses WHERE status = 'REVIEWED'"
            ).fetchone()[0]
            parts = []
            if reviewed:
                parts.append(
                    f"{reviewed} deep-dive thesis(es) await accept/reject"
                    f" ({len(survivors)} new tonight)"
                )
            if pending:
                parts.append(f"{pending} order(s) await approval")
            if rejected:
                parts.append(
                    f"{len(rejected)} dive(s) reviewer-REJECTED"
                    f" ({', '.join(t.strategy_tag for t in rejected)})"
                    " — see reports/deepdive/ for the challenges; those themes"
                    " retry next Friday"
                )
            if parts:
                notify(
                    "Decisions needed" if (pending or reviewed)
                    else "Friday dives: nothing survived review",
                    "; ".join(parts) + ("\nThe desk does nothing until you run:\n"
                                        "uv run python -m ops.approve"
                                        if (pending or reviewed) else ""),
                    priority="high", tags="hourglass_flowing_sand",
                )
            elif cycle_type == "weekly":
                # A no-op Friday is still a result — never a silent one.
                notify(
                    "Friday cycle: no themes due for research",
                    "No accepted theme is awaiting a deep dive and no"
                    " decisions are pending.",
                    priority="default", tags="zzz",
                )
            mark(conn, cycle, "notify")

            # Saturday's 06:15 briefing reads the newest DAILY note, rendered
            # at 23:00 — before tonight's dives existed. Re-render it so the
            # queue/decisions sections reflect what just happened.
            if cycle_type == "weekly" and (survivors or rejected):
                _rerender_daily(conn)

            render_weekly(conn, period_key)
            mark(conn, cycle, "report", status="COMPLETE")
            print(f"{cycle_type} cycle {period_key} COMPLETE: "
                  f"{len(survivors)} thesis(es) REVIEWED, {n_proposed} proposed, "
                  f"{pending} orders pending")
        except Exception as e:
            mark(conn, cycle, cycle.phase or "unknown", status="FAILED", error=str(e)[:1500])
            raise


if __name__ == "__main__":
    arg = sys.argv[1].removeprefix("--trigger=") if len(sys.argv) > 1 else "timer"
    main(arg)
