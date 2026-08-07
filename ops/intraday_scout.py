"""Intraday scout watch (systemd timer, 07/11/15/19 ET, 7 days/week).

Local-only staging pass (research/intraday_watch.py): zero Anthropic spend,
idempotent per batch slot via cycle_log ('intraday', batch_key). Loud on
crash, but never load-bearing — the 23:00 scout degrades to its single-pass
behavior when a batch (or every batch) fails.

    uv run python -m ops.intraday_scout
"""

from datetime import datetime, timezone

from data.db import connect
from ops.alerts import loud
from ops.daily_monitor import claim_cycle, finish_cycle
from research import intraday_watch


@loud("intraday_scout")
def main() -> None:
    now = datetime.now(timezone.utc)
    key = intraday_watch.batch_key(now)
    with connect() as conn:
        cycle = claim_cycle(conn, key, "timer", cycle_type="intraday")
        if cycle is None:
            print(f"intraday batch {key} already COMPLETE — idempotent no-op")
            return
        try:
            summary = intraday_watch.run_batch(conn, now)
        except Exception as e:
            conn.rollback()  # a DB error would poison the FAILED update below
            finish_cycle(conn, cycle, "FAILED", "watch", str(e)[:1500])
            raise
        finish_cycle(conn, cycle, "COMPLETE", "watch")
        print(f"intraday batch {key} OK: session={summary['session']},"
              f" ideas={summary['ideas']}, kept={summary['kept']},"
              f" staged={summary['inserted']}")


if __name__ == "__main__":
    main()
