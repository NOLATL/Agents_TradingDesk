"""Daily monitor (systemd timer, 23:00 ET post-close). Idempotent; loud.

Sequence: session guard → finality guard → ingest → live runner (guards +
genesis replay + incremental persist) → milestone monitor (active theses,
incl. the never-quiet daily status rows) → daily report → intake (absorbed
economics research: resurface due DEFERRED themes, candidate scouts ≤1/day
per lens, nightly theme briefs) → report re-render (scout outcome +
new candidates into tomorrow's briefing) → alerts. Before genesis this
runs in dry-run mode (guards + ingest only).

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


def claim_cycle(conn, period_key: str, trigger: str,
                cycle_type: str = "daily") -> CycleLog | None:
    """cycle_log idempotency claim. Returns None if already COMPLETE."""
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
        conn, CycleLog, "cycle_type = %s AND period_key = %s",
        (cycle_type, period_key),
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


def record_scout_outcome(
    conn, session: date, outcome: str,
    slug: str | None = None, title: str | None = None, detail: str | None = None,
    conviction: str | None = None, review_conviction: str | None = None,
    fallback: bool | None = None,
) -> None:
    """One transparency row per session (migration 006; conviction added in
    007; reviewer conviction + fallback flag in 012). The daily report
    renders the latest row, so the briefing confirms the scout ran even on
    nights when review rejects and nothing enters the queue. Upsert: a
    re-run after a FAILED cycle records its own outcome. Does not commit."""
    conn.execute(
        "INSERT INTO scout_outcomes (session_date, outcome, slug, title, detail,"
        " conviction, review_conviction, fallback)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (session_date) DO UPDATE SET outcome = EXCLUDED.outcome,"
        "  slug = EXCLUDED.slug, title = EXCLUDED.title, detail = EXCLUDED.detail,"
        "  conviction = EXCLUDED.conviction,"
        "  review_conviction = EXCLUDED.review_conviction,"
        "  fallback = EXCLUDED.fallback",
        (session, outcome, slug, title, detail, conviction, review_conviction,
         fallback),
    )


def intake(conn, session: date) -> None:
    """The absorbed economics research pass (runs after the report so a
    research failure never costs the ledger or the briefing input; the
    report is re-rendered afterwards so the scout outcome still reaches
    the next morning's briefing)."""
    from data.adapters import rss as rss_adapter
    from ops.approve import roll_theme_queue
    from research import theme_brief
    from research.candidate_scout import ReviewRejected, scout
    from schemas.models import Theme, Thesis
    from data import repository as repo

    # Queue mechanics: due DEFERRED themes resurface (Mondays); candidates
    # the human passed over defer until next Monday. Reject is terminal and
    # handled at the CLI, not here.
    roll_theme_queue(conn, session)

    rss_adapter.ingest_all()

    # Symbol directory for the proposal precheck (007): weekly Tiingo
    # supported-tickers refresh. Best-effort — a failure skips validation
    # for the night, never blocks the mandatory proposal.
    try:
        from data.adapters.tiingo import refresh_symbol_directory
        refresh_symbol_directory(conn)
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"symbol directory refresh failed (precheck skipped): {e}",
              file=sys.stderr)

    # One mandatory proposal per session (user decision 2026-07-23). Any
    # no-theme outcome besides an idempotent re-run raises and notifies —
    # a silent zero-candidate day is no longer a valid result.
    try:
        theme = scout(conn, session)
        # theme is None only on an idempotent re-run — the earlier run
        # already recorded its outcome; leave that row alone.
        if theme is not None:
            record_scout_outcome(
                conn, session, "PROPOSED", theme.slug, theme.title,
                conviction=theme.research_config.get("conviction"),
                review_conviction=theme.research_config.get("review_conviction"),
                fallback=theme.research_config.get("published_as_fallback"))
        conn.commit()
        if theme is not None:
            seeded = theme.research_config.get("seeded")
            review_conviction = theme.research_config.get("review_conviction")
            conviction_note = (
                f" Reviewer conviction: {review_conviction.upper()}"
                + (" (fallback)." if theme.research_config.get(
                    "published_as_fallback") else ".")
                if review_conviction else "")
            notify(
                (f"New candidate theme (from your seed): {theme.slug}"
                 if seeded else f"New candidate theme: {theme.slug}"),
                f"Scout proposed '{theme.title}'.{conviction_note} "
                f"Review: uv run python -m ops.approve theme list",
                priority="default", tags="bulb",
            )
    except ReviewRejected as e:
        # Commit, not rollback: nothing enters the queue on this path
        # (propose_theme never ran), and the only pending writes are the
        # scout_attempts instrument rows, which must survive the night.
        conn.commit()
        record_scout_outcome(conn, session, "REJECTED",
                             e.proposal.slug, e.proposal.title, e.critique,
                             conviction=e.proposal.conviction)
        conn.commit()
        seed_note = ""
        if getattr(e, "seed_id", None) is not None:
            seed_note = (
                f"Your seed idea failed review — seed #{e.seed_id} consumed;"
                f" re-queue with `ops.approve idea add` to retry. "
            )
        notify(
            ("Your seed idea failed review — no candidate today"
             if seed_note else
             "Scout proposal failed review — no candidate today"),
            seed_note + str(e), priority="high", tags="warning",
        )
    except Exception as e:  # noqa: BLE001 — research must not cost the cycle
        # Ollama down, schema-invalid output after retries, EDGAR timeout…
        # The ledger and report are already safe; a scout failure is a loud
        # notification, never a FAILED cycle (which would not auto-retry).
        conn.rollback()
        record_scout_outcome(conn, session, "ERROR",
                             detail=f"{type(e).__name__}: {str(e)[:800]}")
        conn.commit()
        notify(
            "Scout crashed — no candidate today",
            f"{type(e).__name__}: {str(e)[:400]}",
            priority="high", tags="warning",
        )

    # ~Monthly dynamic EDGAR-term proposals (2026-08-07 P4): local Qwen only,
    # self-throttled inside propose_terms (25-day cooldown). Failure is a
    # stderr note, never a blocked cycle or a lost proposal night.
    try:
        from research import term_scout
        from schemas.models import Experiment
        exp = repo.fetch_one(conn, Experiment, "name = 'EXP-001'")
        if exp is not None:
            proposed = term_scout.propose_terms(conn, session,
                                                experiment_id=exp.id)
            conn.commit()
            if proposed:
                notify(
                    "EDGAR trend-term proposals pending",
                    f"{proposed} new term(s) proposed for the scout's scan."
                    " Decide: uv run python -m ops.approve terms list",
                    priority="default", tags="bulb",
                )
    except Exception as e:  # noqa: BLE001 — enrichment, never load-bearing
        conn.rollback()
        print(f"term proposer failed (skipped): {type(e).__name__}: {e}",
              file=sys.stderr)

    for thesis in repo.fetch(conn, Thesis, "status = 'ACTIVE' ORDER BY id"):
        if thesis.theme_id is None:
            continue
        theme = repo.fetch_one(conn, Theme, "id = %s", (thesis.theme_id,))
        if theme is None:
            continue
        try:
            theme_brief.write_brief(conn, theme, thesis, session)
        except Exception as e:  # noqa: BLE001 — same rationale as the scouts
            conn.rollback()
            notify(
                f"Theme brief crashed ({theme.slug})",
                f"{type(e).__name__}: {str(e)[:400]}",
                priority="high", tags="warning",
            )


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
                from ops.milestone_monitor import check_milestones, record_daily_status
                results = check_milestones(conn, session)
                # Never silently quiet: a status row per ACTIVE thesis, always.
                record_daily_status(conn, session, results)

            phase = "report"
            from reporting.daily import render_daily
            render_daily(conn, session, summary)

            phase = "intake"
            if not summary.dry_run:
                intake(conn, session)
                # Final render: same deterministic report, now carrying the
                # scout outcome and any new candidate — so tomorrow MORNING's
                # briefing shows tonight's research, not yesterday's. The
                # pre-intake render above stays as the crash-safe floor.
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
