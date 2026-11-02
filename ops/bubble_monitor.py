"""AI-bubble exit monitor (DECISIONS.md 2026-11-01; plan in
docs/themes/monitor-ai-bubble-exit.md). OPERATOR-ONLY, quarantined from
the analyst (invariant 5): the human's personal de-risking dashboard.

    uv run python -m ops.bubble_monitor run [--as-of YYYY-MM-DD]   # nightly (timer)
    uv run python -m ops.bubble_monitor show [--no-color]          # dashboard, cache-only
    uv run python -m ops.bubble_monitor watch [SECONDS]            # dashboard, redraws
    uv run python -m ops.bubble_monitor score C YELLOW "MSFT guided 2027 capex growth below 2026"
    uv run python -m ops.bubble_monitor score C CLEARED "withdrawn"
    uv run python -m ops.bubble_monitor hard-red raise oracle_junk "S&P cut ORCL to BB+"
    uv run python -m ops.bubble_monitor hard-red clear oracle_junk "upgraded back"
    uv run python -m ops.bubble_monitor hard-red list
    uv run python -m ops.bubble_monitor history

`run` ingests FRED, Tiingo, SEC XBRL, Azure, FINRA and Census (cached
immutably via fetch_and_cache, invariant 4; Vast is display-only by license),
computes, upserts one bubble_runs row for the session, writes the vault note,
and pushes ntfy only when the stage moves. Tiingo is the price source so the
dashboard does not depend on the weekly Schwab re-auth. Idempotent: re-running a session recomputes and overwrites the same
row/files. A dark vendor degrades its signals to NO_DATA (rendered loudly)
— never a synthesized value, never a failed run. `show` never touches the
network: it recomputes from cached documents + human tables.

Every number here is deterministic Python; no model is involved.
"""

import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from analytics.bubble_monitor import (
    HumanScore,
    composite,
    derive_fundamentals,
    evaluate_at,
    held_stages,
    link_statuses,
    next_escalation,
    replay,
    stage,
)
from config.loader import CONFIG
from data import repository as repo
from data.adapters import bubble_feeds as feeds
from data.adapters import fred, tiingo
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
Dated = list[tuple[date, Decimal]]


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

def _contained(conn, label: str, fn) -> str | None:
    try:
        fn()
        conn.commit()
        return None
    except Exception as e:  # noqa: BLE001 — one feed must not cost the dashboard
        conn.rollback()
        return f"{label} fetch failed ({type(e).__name__})"


def ingest(conn) -> list[str]:
    gaps = [_contained(conn, f"FRED {sid}", lambda sid=sid: fred.ingest_series(conn, sid))
            for sid in CFG.get("fred_series", {})]
    gaps += _ingest_tiingo(conn)
    gaps += _ingest_xbrl(conn)
    gaps += [_contained(conn, "Azure prices", lambda: feeds.ingest_azure(conn)),
             _contained(conn, "FINRA margin", lambda: feeds.ingest_finra(conn)),
             _contained(conn, "Census BTOS", lambda: feeds.ingest_btos(conn)),
             _contained(conn, "Vast (display)", feeds.refresh_vast)]
    return [g for g in gaps if g]


def _newest_price_doc(conn, symbol: str) -> Document | None:
    """Newest full-history doc from the daily job or the scan, whichever is
    fresher. LIMIT 1: only the newest document's bytes are loaded."""
    docs = repo.fetch(conn, Document,
                      "source_name IN ('tiingo', 'tiingo_scan') AND retrieval_url LIKE %s"
                      " ORDER BY ingestion_time DESC LIMIT 1",
                      (f"%/daily/{symbol.lower()}/prices",))
    return docs[0] if docs else None


def _ingest_tiingo(conn) -> list[str]:
    """Reuses tonight's daily/scan documents; fetches only what they lack, so
    the free tier's 50-symbols/hour cap is respected. Cached under tiingo_scan
    (never enters price_bars). 429 stops the loop, no retry (contract)."""
    fresh_after = datetime.now(timezone.utc) - timedelta(hours=20)
    gaps = []
    for sym in CFG.get("tiingo_symbols", []):
        doc = _newest_price_doc(conn, sym)
        if doc is not None and doc.ingestion_time > fresh_after:
            continue
        try:
            tiingo.ingest_scan_symbol(conn, sym)
            conn.commit()
        except httpx.HTTPStatusError as e:
            conn.rollback()
            gaps.append(f"Tiingo {sym} fetch failed (HTTP {e.response.status_code})")
            if e.response.status_code == 429:
                break
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            gaps.append(f"Tiingo {sym} fetch failed ({type(e).__name__})")
    return gaps


def _ingest_xbrl(conn) -> list[str]:
    x = CFG["xbrl"]
    tags = sorted({t for ts in x["tags"].values() for t in ts})
    gaps = []
    for sym, cik in x["ciks"].items():
        for tag in tags:
            try:
                feeds.ingest_xbrl(conn, sym, cik, tag)
                conn.commit()
            except httpx.HTTPStatusError as e:
                conn.rollback()
                if e.response.status_code != 404:     # 404 = company never used this tag
                    gaps.append(f"XBRL {sym} {tag} (HTTP {e.response.status_code})")
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                gaps.append(f"XBRL {sym} {tag} ({type(e).__name__})")
            time.sleep(0.15)                          # SEC fair-access: <10 req/s
    return gaps


# --------------------------------------------------------------------------- #
# Read cached series (no network)
# --------------------------------------------------------------------------- #

def load_series(conn) -> dict[str, Dated]:
    """Every named series, ascending, dated. Pure read of cached documents."""
    dated: dict[str, Dated] = {}
    for sid in CFG.get("fred_series", {}):
        docs = repo.fetch(conn, Document,
                          "source_name = 'fred' AND query_params->>'series_id' = %s"
                          " ORDER BY ingestion_time DESC LIMIT 1", (sid,))
        dated[sid] = sorted((o.observation_date, Decimal(str(o.value)))
                            for o in (fred.parse_observations(docs[0], sid) if docs else [])
                            if o.value is not None)
    for sym in CFG.get("tiingo_symbols", []):
        doc = _newest_price_doc(conn, sym)
        dated[sym] = [(b.session, b.adj_close)
                      for b in (tiingo.parse_bars(doc, sym) if doc else [])]
    x = CFG["xbrl"]
    facts: dict[str, dict[str, list]] = {}
    for sym in x["ciks"]:
        facts[sym] = {}
        for metric, tags in x["tags"].items():
            found = [f for f in (feeds.load_xbrl_facts(conn, sym, t) for t in tags) if f]
            if found:
                facts[sym][metric] = max(found, key=lambda fs: max(f["end"] for f in fs))
    dated.update(derive_fundamentals(facts, x["hyperscalers"], x["rpo_names"]))
    dated.update(feeds.azure_series(conn))
    margin = feeds.latest_raw(conn, "finra_margin")
    dated["finra_margin"] = feeds.parse_finra(margin) if margin else []
    m = dated["finra_margin"]
    dated["finra_margin_yoy"] = [(m[i][0], m[i][1] / m[i - 12][1] - 1)
                                 for i in range(12, len(m))]
    btos = feeds.latest_raw(conn, "census_btos")
    raw = feeds.parse_btos(btos) if btos else []
    dated["btos_ai_use"] = [(raw[i][0], sum(v for _, v in raw[i - 2:i + 1]) / 3)
                            for i in range(2, len(raw))]   # 3-cycle mean: survey is noisy
    return dated


def load_human(conn) -> tuple[list[HumanScore], list[tuple], list[HardRed]]:
    """(all human rows ascending, all hard-red rows, active hard reds)."""
    rows = [HumanScore(link, status, note, recorded_on)
            for link, status, note, recorded_on in conn.execute(
                "SELECT link, status, note, recorded_on FROM bubble_human_scores"
                " ORDER BY recorded_at, id")]
    hard_rows = conn.execute(
        "SELECT key, raised_on, cleared_on FROM bubble_hard_reds ORDER BY raised_on").fetchall()
    keys = CFG.get("hard_red_keys", {})
    active = [HardRed(k, keys.get(k, "(unknown key)"), note, raised_on)
              for k, note, raised_on in conn.execute(
                  "SELECT key, note, raised_on FROM bubble_hard_reds"
                  " WHERE cleared_on IS NULL ORDER BY raised_on, key")]
    return rows, hard_rows, active


def current_human(rows: list[HumanScore]) -> dict[str, HumanScore]:
    out: dict[str, HumanScore] = {}
    for row in rows:
        if row.status == "CLEARED":
            out.pop(row.link, None)
        else:
            out[row.link] = row
    return out


def previous_stage(conn, session: date) -> int | None:
    row = conn.execute("SELECT stage FROM bubble_runs WHERE session < %s"
                       " ORDER BY session DESC LIMIT 1", (session,)).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- #
# Compute (pure over loaded inputs)
# --------------------------------------------------------------------------- #

def stale_gaps(dated: dict[str, Dated], today: date) -> list[str]:
    """A dead feed must be loud: the Schwab token sat expired for five weeks
    with nobody noticing."""
    limit = int(CFG.get("market_stale_days", 5))
    daily = list(CFG.get("tiingo_symbols", [])) + ["BAMLH0A0HYM2", "BAMLC0A0CM", "DFII10"]
    gaps = []
    for name in daily:
        points = dated.get(name) or []
        if not points:
            gaps.append(f"{name}: no data")
        elif (today - points[-1][0]).days > limit:
            gaps.append(f"{name}: stale, last {points[-1][0]}")
    return gaps


def compute(conn, session: date, gaps: list[str] | None = None) -> BubbleReport:
    dated = load_series(conn)
    specs = CFG.get("signals", [])
    signals = evaluate_at(session, dated, specs)
    rows, hard_rows, hard_reds = load_human(conn)
    max_age = int(CFG.get("human_max_age_days", 35))
    links = link_statuses(signals, current_human(rows), today=session,
                          human_max_age_days=max_age)
    confirm = CFG.get("confirm_signal_ids", [])
    result = stage(links, [h.key for h in hard_reds], signals, confirm_signal_ids=confirm)
    score = composite(links, len(hard_reds), CFG["score"])
    sessions = [d for d, _ in (dated.get("SPY") or []) if d <= session]
    window = sessions[-int(CFG.get("history_sessions", 130)):]
    if not window or window[-1] != session:
        window = window + [session]
    history = replay(window, dated, specs, rows, hard_rows, score_cfg=CFG["score"],
                     human_max_age_days=max_age, confirm_signal_ids=confirm)
    held = held_stages([p.stage for p in history],
                       confirm_up=int(CFG.get("stage_confirm_up", 5)),
                       confirm_down=int(CFG.get("stage_confirm_down", 10)),
                       floors=[1 if p.hard_reds else 0 for p in history])
    vast, vast_as_of = feeds.read_vast(CFG.get("vast_models", []))
    return BubbleReport(
        session=session, links=links, result=result, stage=held[-1], hard_reds=hard_reds,
        previous_stage=previous_stage(conn, session),
        gaps=list(gaps or []) + stale_gaps(dated, session),
        score=score, history=history,
        escalation=next_escalation(result, links, CFG["score"]),
        tranche_pct=CFG.get("tranche_pct"), vast=vast, vast_as_of=vast_as_of)


def persist(conn, report: BubbleReport) -> None:
    payload = {
        "links": {l.link: {"status": l.status, "auto": l.auto, "human": l.human,
                           "human_stale": l.human_stale} for l in report.links},
        "signals": {s.id: {"status": s.status,
                           "measure": None if s.measure is None else str(s.measure),
                           "severity": None if s.severity is None else str(s.severity),
                           "detail": s.detail,
                           "as_of": None if s.as_of is None else s.as_of.isoformat()}
                    for l in report.links for s in l.signals},
        "link_scores": {k: str(v) for k, v in report.score.links.items()},
        "band": report.score.band,
        "raw_stage": report.result.stage,
        "hard_reds": [h.key for h in report.hard_reds],
        "reasons": list(report.result.reasons),
        "market_confirmed": report.result.market_confirmed,
        "gaps": report.gaps,
    }
    conn.execute(
        "INSERT INTO bubble_runs (session, stage, red_links, score, coverage, payload,"
        " code_version) VALUES (%s, %s, %s, %s, %s, %s, %s)"
        " ON CONFLICT (session) DO UPDATE SET stage = EXCLUDED.stage,"
        " red_links = EXCLUDED.red_links, score = EXCLUDED.score,"
        " coverage = EXCLUDED.coverage, payload = EXCLUDED.payload,"
        " code_version = EXCLUDED.code_version, computed_at = now()",
        (report.session, report.stage, list(report.result.red_links),
         report.score.score, report.score.coverage, json.dumps(payload), code_version()))
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
    if len(report.gaps) >= int(CFG.get("gap_alert_count", 5)):
        notify("AI-bubble monitor: feeds degraded", "; ".join(report.gaps)[:400], priority="high")


def show(color: bool) -> None:
    with connect() as conn:
        row = conn.execute("SELECT max(session) FROM bubble_runs").fetchone()
        session = row[0] or _session(None)
        report = compute(conn, session)
    print(render_terminal(report, color=color))


def watch(seconds: int) -> None:
    while True:
        print("\033[2J\033[H", end="")
        show(color=True)
        time.sleep(seconds)


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
        for session, stg, sc, reds in conn.execute(
                "SELECT session, stage, score, red_links FROM bubble_runs"
                " ORDER BY session DESC LIMIT %s", (limit,)):
            print(f"{session}  score {sc:.0f}  stage {stg}  red: {','.join(reds) or '—'}")


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
    elif cmd == "watch":
        watch(int(rest[0]) if rest else 300)
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
