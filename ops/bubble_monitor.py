"""AI-bubble exit monitor (DECISIONS.md 2026-11-01; plan in
docs/themes/monitor-ai-bubble-exit.md). OPERATOR-ONLY, quarantined from
the analyst (invariant 5): the human's personal de-risking dashboard.

    uv run python -m ops.bubble_monitor run [--as-of YYYY-MM-DD]   # nightly (timer)
    uv run python -m ops.bubble_monitor show [--no-color]          # terminal, cache-only
    uv run python -m ops.bubble_monitor score C YELLOW "MSFT guided 2027 capex growth below 2026"
    uv run python -m ops.bubble_monitor score C CLEARED "withdrawn"
    uv run python -m ops.bubble_monitor hard-red raise oracle_junk "S&P cut ORCL to BB+"
    uv run python -m ops.bubble_monitor hard-red clear oracle_junk "upgraded back"
    uv run python -m ops.bubble_monitor hard-red list
    uv run python -m ops.bubble_monitor history

`run` ingests FRED + Schwab (every response cached immutably via
fetch_and_cache, invariant 4), computes, upserts one bubble_runs row for
the session, writes the vault note, and pushes ntfy only when the stage
moves. Idempotent: re-running a session recomputes and overwrites the same
row/files. A dark vendor degrades its signals to NO_DATA (rendered loudly)
— never a synthesized value, never a failed run. `show` never touches the
network: it recomputes from cached documents + human tables.

Every number here is deterministic Python; no model is involved.
"""

import json
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal

from analytics.bubble_monitor import (
    HumanScore,
    evaluate_signal,
    link_statuses,
    stage,
)
from config.loader import CONFIG
from data import repository as repo
from data.adapters import fred
from data.db import connect
from ops.alerts import loud, notify
from reporting.bubble_monitor import (
    BubbleReport,
    HardRed,
    render_markdown,
    render_terminal,
    stage_change_message,
    write_report,
)
from schemas.models import Document

CFG = CONFIG.get("bubble_monitor", {})
LINK_CODES = set("ABCDEFGHI")
HUMAN_STATUSES = {"GREEN", "YELLOW", "RED", "CLEARED"}
PURPOSE = "bubble_monitor"          # schwab_history stored-param tag


def code_version() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5,
                              ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# --------------------------------------------------------------------------- #
# Ingest (network) — per-series containment
# --------------------------------------------------------------------------- #

def ingest(conn) -> list[str]:
    """Fetch every configured series. Returns gap notes; never raises for a
    single series (one bad series must not cost the dashboard)."""
    gaps: list[str] = []
    for sid in CFG.get("fred_series", {}):
        try:
            fred.ingest_series(conn, sid)
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            gaps.append(f"FRED {sid} fetch failed ({type(e).__name__})")
    gaps += _ingest_schwab(conn)
    return gaps


def _ingest_schwab(conn) -> list[str]:
    if not CONFIG.get("schwab", {}).get("enabled"):
        return ["Schwab disabled in config — market signals NO_DATA"]
    from data.adapters.schwab import ingest_tagged_history
    from data.adapters.schwab_auth import SchwabDark

    rps = CONFIG["schwab"].get("throttle_rps", 2)
    gaps = []
    for sym in CFG.get("schwab_symbols", []):
        try:
            # Always refetch (nightly freshness); identical responses
            # dedupe by content hash in fetch_and_cache.
            ingest_tagged_history(conn, sym, period_years=CFG.get("schwab_history_years", 1),
                                  purpose=PURPOSE)
            conn.commit()
        except SchwabDark as e:
            conn.rollback()
            return gaps + [f"Schwab dark ({e}) — market signals use the last cached history"]
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            gaps.append(f"Schwab {sym} fetch failed ({type(e).__name__})")
        time.sleep(1.0 / rps if rps else 0)
    return gaps


# --------------------------------------------------------------------------- #
# Read cached series (no network)
# --------------------------------------------------------------------------- #

def load_series(conn) -> tuple[dict[str, list[Decimal]], dict[str, date | None]]:
    series: dict[str, list[Decimal]] = {}
    as_of: dict[str, date | None] = {}
    for sid in CFG.get("fred_series", {}):
        docs = repo.fetch(conn, Document,
                          "source_name = 'fred' AND query_params->>'series_id' = %s"
                          " ORDER BY ingestion_time DESC LIMIT 1", (sid,))
        obs = sorted((o for o in (fred.parse_observations(docs[0], sid) if docs else [])
                      if o.value is not None), key=lambda o: o.observation_date)
        series[sid] = [Decimal(str(o.value)) for o in obs]
        as_of[sid] = obs[-1].observation_date if obs else None
    from data.adapters.schwab import latest_tagged_history
    for sym in CFG.get("schwab_symbols", []):
        candles = latest_tagged_history(conn, sym, purpose=PURPOSE)
        series[sym] = [c.close for c in candles]
        as_of[sym] = candles[-1].session if candles else None
    return series, as_of


def load_human(conn) -> tuple[dict[str, HumanScore], list[HardRed]]:
    scores = {}
    for link, status, note, recorded_on in conn.execute(
        "SELECT DISTINCT ON (link) link, status, note, recorded_on"
        " FROM bubble_human_scores ORDER BY link, recorded_at DESC, id DESC"
    ):
        if status != "CLEARED":
            scores[link] = HumanScore(link, status, note, recorded_on)
    keys = CFG.get("hard_red_keys", {})
    reds = [HardRed(k, keys.get(k, "(unknown key)"), note, raised_on)
            for k, note, raised_on in conn.execute(
                "SELECT key, note, raised_on FROM bubble_hard_reds"
                " WHERE cleared_on IS NULL ORDER BY raised_on, key")]
    return scores, reds


def previous_stage(conn, session: date) -> int | None:
    row = conn.execute("SELECT stage FROM bubble_runs WHERE session < %s"
                       " ORDER BY session DESC LIMIT 1", (session,)).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Compute (pure over loaded inputs)
# --------------------------------------------------------------------------- #

def compute(conn, session: date, gaps: list[str] | None = None) -> BubbleReport:
    series, as_of = load_series(conn)
    signals = [evaluate_signal(spec, series, as_of) for spec in CFG.get("signals", [])]
    human, hard_reds = load_human(conn)
    links = link_statuses(signals, human, today=session,
                          human_max_age_days=int(CFG.get("human_max_age_days", 35)))
    result = stage(links, [h.key for h in hard_reds], signals,
                   confirm_signal_ids=CFG.get("confirm_signal_ids", []))
    return BubbleReport(session=session, links=links, result=result,
                        hard_reds=hard_reds, previous_stage=previous_stage(conn, session),
                        gaps=list(gaps or []))


def persist(conn, report: BubbleReport) -> None:
    payload = {
        "links": {l.link: {"status": l.status, "auto": l.auto, "human": l.human,
                           "human_stale": l.human_stale} for l in report.links},
        "signals": {s.id: {"status": s.status,
                           "measure": None if s.measure is None else str(s.measure),
                           "detail": s.detail,
                           "as_of": None if s.as_of is None else s.as_of.isoformat()}
                    for l in report.links for s in l.signals},
        "hard_reds": [h.key for h in report.hard_reds],
        "reasons": list(report.result.reasons),
        "market_confirmed": report.result.market_confirmed,
        "gaps": report.gaps,
    }
    conn.execute(
        "INSERT INTO bubble_runs (session, stage, red_links, payload, code_version)"
        " VALUES (%s, %s, %s, %s, %s)"
        " ON CONFLICT (session) DO UPDATE SET stage = EXCLUDED.stage,"
        " red_links = EXCLUDED.red_links, payload = EXCLUDED.payload,"
        " code_version = EXCLUDED.code_version, computed_at = now()",
        (report.session, report.result.stage, list(report.result.red_links),
         json.dumps(payload), code_version()))
    conn.commit()


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _session(as_of: str | None) -> date:
    if as_of:
        return date.fromisoformat(as_of)
    from ops.daily_monitor import latest_closed_session
    return latest_closed_session(datetime.now(timezone.utc)) or date.today()


@loud("bubble_monitor")
def run(as_of: str | None = None) -> None:
    if not CFG.get("enabled", False):
        print("bubble_monitor disabled in config")
        return
    session = _session(as_of)
    with connect() as conn:
        gaps = ingest(conn)
        report = compute(conn, session, gaps)
        persist(conn, report)
    path = write_report(render_markdown(report), session)
    msg = stage_change_message(report)
    if msg:
        notify(msg[0], msg[1], priority=msg[2], tags="chart_with_downwards_trend")
    print(render_terminal(report, color=False))
    print(f"\nwrote {path}")


def show(color: bool) -> None:
    with connect() as conn:
        row = conn.execute("SELECT max(session) FROM bubble_runs").fetchone()
        session = row[0] or _session(None)
        report = compute(conn, session)
    print(render_terminal(report, color=color))


def score(link: str, status: str, note: str) -> None:
    link, status = link.upper(), status.upper()
    if link not in LINK_CODES:
        sys.exit(f"link must be one of {''.join(sorted(LINK_CODES))}")
    if status not in HUMAN_STATUSES:
        sys.exit(f"status must be one of {sorted(HUMAN_STATUSES)}")
    if not note.strip():
        sys.exit("a note (your rationale) is required — HUMAN provenance")
    with connect() as conn:
        conn.execute("INSERT INTO bubble_human_scores (link, status, note, recorded_on)"
                     " VALUES (%s, %s, %s, %s)", (link, status, note.strip(), date.today()))
        conn.commit()
    print(f"recorded {link} = {status}")


def hard_red(action: str, key: str | None, note: str | None) -> None:
    keys = CFG.get("hard_red_keys", {})
    with connect() as conn:
        if action == "list":
            for k, desc in keys.items():
                row = conn.execute("SELECT note, raised_on FROM bubble_hard_reds"
                                   " WHERE key = %s AND cleared_on IS NULL", (k,)).fetchone()
                state = f"ACTIVE since {row[1]}: {row[0]}" if row else "clear"
                print(f"{k:<20} {state}\n{'':<20} {desc}")
            return
        if key not in keys:
            sys.exit(f"unknown key {key!r}; one of: {', '.join(keys)}")
        if not (note or "").strip():
            sys.exit("a note is required — HUMAN provenance")
        if action == "raise":
            conn.execute("INSERT INTO bubble_hard_reds (key, note, raised_on)"
                         " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                         (key, note.strip(), date.today()))
        elif action == "clear":
            conn.execute("UPDATE bubble_hard_reds SET cleared_on = %s, cleared_note = %s"
                         " WHERE key = %s AND cleared_on IS NULL",
                         (date.today(), note.strip(), key))
        else:
            sys.exit("action must be raise | clear | list")
        conn.commit()
    print(f"hard red {key}: {action}d")


def history(limit: int = 30) -> None:
    with connect() as conn:
        for session, stg, reds in conn.execute(
                "SELECT session, stage, red_links FROM bubble_runs"
                " ORDER BY session DESC LIMIT %s", (limit,)):
            print(f"{session}  stage {stg}  red: {','.join(reds) or '—'}")


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "show"
    rest = argv[1:]
    if cmd == "run":
        as_of = next((a.removeprefix("--as-of=") for a in rest if a.startswith("--as-of=")), None)
        if "--as-of" in rest:
            as_of = rest[rest.index("--as-of") + 1]
        run(as_of)
    elif cmd == "show":
        show(color="--no-color" not in rest and sys.stdout.isatty())
    elif cmd == "score" and len(rest) >= 3:
        score(rest[0], rest[1], " ".join(rest[2:]))
    elif cmd == "hard-red" and rest:
        hard_red(rest[0], rest[1] if len(rest) > 1 else None,
                 " ".join(rest[2:]) if len(rest) > 2 else None)
    elif cmd == "history":
        history()
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
