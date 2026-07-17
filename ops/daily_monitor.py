"""Daily monitor (systemd timer, 23:00 ET post-close). Idempotent; loud.

Sequence: session guard → finality guard → ingest → live runner (guards +
genesis replay + incremental persist) → milestone monitor (active theses) →
daily report → alerts. Before genesis this runs in dry-run mode (guards +
ingest only) so clean unattended sessions accumulate for Gate C.

    uv run python -m ops.daily_monitor [--as-of YYYY-MM-DD]
"""

import sys
from datetime import date, datetime, timezone

from analytics.engine import clock
from config.loader import CONFIG
from data import repository as repo
from data.adapters import edgar, fred, tiingo
from data.db import connect
from data.price_bars import _latest_docs_by_symbol
from data.adapters.tiingo import parse_bars
from ops.alerts import loud, notify
from ops import live_run
from schemas.models import CycleLog


def latest_closed_session(now: datetime) -> date | None:
    """The most recent session whose close is in the past."""
    day = now.date()
    for _ in range(7):
        if clock.is_session(day) and clock.session_close_utc(day) <= now:
            return day
        day = day.__class__.fromordinal(day.toordinal() - 1)
    return None


def finality_guard(conn, session: date) -> None:
    """The freshly fetched SPY document must contain the target session's bar

    — protects against early runs and Tiingo lag."""
    spy = CONFIG["engine"]["controls"]["spy_symbol"]
    doc = _latest_docs_by_symbol(conn).get(spy)
    if doc is None or not any(b.session == session for b in parse_bars(doc, spy)):
        raise RuntimeError(
            f"finality guard: {spy} document lacks a bar for {session} — "
            f"Tiingo EOD not final; halting (re-run later)"
        )


def claim_cycle(conn, period_key: str, trigger: str) -> CycleLog | None:
    """cycle_log idempotency claim. Returns None if already COMPLETE."""
    row = conn.execute(
        "INSERT INTO cycle_log (cycle_type, period_key, trigger, status, started_at)"
        " VALUES ('daily', %s, %s, 'RUNNING', %s)"
        " ON CONFLICT (cycle_type, period_key) DO NOTHING RETURNING id",
        (period_key, trigger, datetime.now(timezone.utc)),
    ).fetchone()
    if row is not None:
        conn.commit()
        return repo.fetch_one(conn, CycleLog, "id = %s", (row[0],))
    existing = repo.fetch_one(
        conn, CycleLog, "cycle_type = 'daily' AND period_key = %s", (period_key,)
    )
    if existing.status == "COMPLETE":
        return None
    # FAILED or stale RUNNING: reclaim
    conn.execute(
        "UPDATE cycle_log SET status='RUNNING', error=NULL, started_at=%s WHERE id=%s",
        (datetime.now(timezone.utc), existing.id),
    )
    conn.commit()
    return existing


def finish_cycle(conn, cycle: CycleLog, status: str, phase: str, error: str | None = None):
    conn.execute(
        "UPDATE cycle_log SET status=%s, phase=%s, finished_at=%s, error=%s WHERE id=%s",
        (status, phase, datetime.now(timezone.utc), error, cycle.id),
    )
    conn.commit()


@loud("daily_monitor")
def main(as_of_override: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    session = (
        date.fromisoformat(as_of_override) if as_of_override else latest_closed_session(now)
    )
    if session is None:
        print("no closed session in the last week — nothing to do")
        return

    with connect() as conn:
        cycle = claim_cycle(conn, session.isoformat(), "timer")
        if cycle is None:
            print(f"daily cycle for {session} already COMPLETE — idempotent no-op")
            return
        phase = "start"
        try:
            phase = "ingest"
            tiingo.ingest_all()
            edgar.ingest_all()
            fred.ingest_all()
            finality_guard(conn, session)

            phase = "live_run"
            summary = live_run.run(conn, session)

            phase = "milestones"
            if not summary.dry_run:
                from ops.milestone_monitor import check_milestones
                check_milestones(conn, session)

            phase = "report"
            from reporting.daily import render_daily
            render_daily(conn, session, summary)

            finish_cycle(conn, cycle, "COMPLETE", phase)
            mode = "dry-run" if summary.dry_run else "live"
            print(f"daily monitor {session} OK ({mode}): nav={summary.nav}, "
                  f"new_trades={summary.persisted_new_trades}, pins={summary.pinned_symbols}")
            if summary.tainted:
                notify("NAV TAINTED", f"contains_forced_resolution active as of {session}",
                       priority="high", tags="warning")
        except Exception as e:
            finish_cycle(conn, cycle, "FAILED", phase, str(e)[:1500])
            raise


if __name__ == "__main__":
    main(sys.argv[1].removeprefix("--as-of=") if len(sys.argv) > 1 else None)
