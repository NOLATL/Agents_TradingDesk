"""Momentum scan (systemd timer, Tue–Sat 00:30 ET — its own Tiingo free-tier
rate-limit hour, after the 23:00 daily monitor). Idempotent; loud; OPERATOR-
ONLY and quarantined (invariant 5, DECISIONS.md 2026-07-29 "Momentum scan").

Sequence under a ('scan', session) cycle_log claim: constituents refresh
(best-effort) → ETF ingest under source_name='tiingo_scan' (never enters
price_bars or any engine surface; per-symbol containment; hard fetch cap;
429 stops the loop, no retry) → compute (pure analytics/scan.py; prior-scan
deltas recomputed from the same immutable documents truncated at the prior
session — no stored scan state) → guarded narration (best-effort) → render.
Scan degradation is a report fact, not a failure: gaps render, the cycle
still COMPLETEs, and nothing here can touch the core daily cycle.

    uv run python -m ops.momentum_scan [--as-of YYYY-MM-DD]
"""

import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from analytics.engine import clock
from analytics.scan import rank_by_composite, rank_deltas, scan_scores
from analytics.scan_internals import index_breadth, member_composites, pocket_internals
from config.loader import CONFIG
from data import repository as repo
from data.adapters import wikipedia
from data.adapters.tiingo import ingest_scan_symbol, latest_scan_bars
from data.db import connect
from ops.alerts import loud, notify
from ops.daily_monitor import latest_closed_session
from reporting.momentum_scan import (
    PocketRow,
    ScanReportData,
    ThemeRow,
    narrator_inputs,
    render_scan,
    write_scan_report,
)
from schemas.models import CycleLog

SCAN_CFG = CONFIG.get("scan", {})


def claim_scan_cycle(conn, period_key: str, trigger: str) -> CycleLog | None:
    """cycle_log idempotency claim for cycle_type='scan' (migration 009);
    same semantics as ops.daily_monitor.claim_cycle."""
    row = conn.execute(
        "INSERT INTO cycle_log (cycle_type, period_key, trigger, status, started_at)"
        " VALUES ('scan', %s, %s, 'RUNNING', %s)"
        " ON CONFLICT (cycle_type, period_key) DO NOTHING RETURNING id",
        (period_key, trigger, datetime.now(timezone.utc)),
    ).fetchone()
    if row is not None:
        conn.commit()
        return repo.fetch_one(conn, CycleLog, "id = %s", (row[0],))
    existing = repo.fetch_one(
        conn, CycleLog, "cycle_type = 'scan' AND period_key = %s", (period_key,)
    )
    if existing.status == "COMPLETE":
        return None
    conn.execute(
        "UPDATE cycle_log SET status='RUNNING', error=NULL, started_at=%s WHERE id=%s",
        (datetime.now(timezone.utc), existing.id),
    )
    conn.commit()
    return existing


def finish_scan_cycle(conn, cycle: CycleLog, status: str, phase: str,
                      error: str | None = None) -> None:
    conn.execute(
        "UPDATE cycle_log SET status=%s, phase=%s, finished_at=%s, error=%s WHERE id=%s",
        (status, phase, datetime.now(timezone.utc), error, cycle.id),
    )
    conn.commit()


def prior_scan_session(session: date) -> date | None:
    sessions = clock.sessions_between(session - timedelta(days=30), session)
    return sessions[-2] if len(sessions) >= 2 else None


def _refresh_constituents(conn) -> str | None:
    """Best-effort weekly snapshot refresh. Returns a gap note on failure;
    alerts only when the surviving snapshot is truly stale."""
    try:
        wikipedia.refresh_constituents(
            conn, max_age_days=SCAN_CFG.get("constituents_max_age_days", 7))
        return None
    except Exception as e:  # noqa: BLE001 — reference data must not cost the scan
        conn.rollback()
        note = f"constituents refresh failed: {type(e).__name__}: {str(e)[:200]}"
        print(f"momentum scan: {note}", file=sys.stderr)
        refreshed = wikipedia.snapshot_refreshed_at(conn)
        stale_days = SCAN_CFG.get("constituents_stale_alert_days", 21)
        if refreshed is None or refreshed < datetime.now(timezone.utc) - timedelta(days=stale_days):
            notify("S&P 500 constituents snapshot stale",
                   f"{note}; last snapshot: {refreshed or 'never'}",
                   priority="high", tags="warning")
        return note


def _nightly_symbols(conn) -> set[str]:
    """Symbols the 23:00 daily job already fetches (theme universe +
    benchmarks) — the scan reads their bars from price_bars, zero extra
    Tiingo requests."""
    controls = CONFIG.get("engine", {}).get("controls", {})
    symbols = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM theme_symbols")}
    symbols.update(s for s in (controls.get("spy_symbol"), controls.get("ewp_symbol")) if s)
    return symbols


def _ingest_etfs(conn, tickers: list[str]) -> tuple[int, int, list[tuple[str, str]]]:
    """(fetched, cache_hits, gaps). Per-symbol containment; the fetch cap and
    the 429 halt-no-retry rule (contracts/tiingo.yaml) are the free-tier
    guards — remaining symbols are SKIPPED, never retried tonight."""
    cap = SCAN_CFG.get("max_fetches_per_run", 45)
    fetched = cache_hits = attempts = 0
    gaps: list[tuple[str, str]] = []
    for i, symbol in enumerate(tickers):
        if attempts >= cap:
            gaps += [(s, "SKIPPED (fetch cap)") for s in tickers[i:]]
            break
        attempts += 1
        try:
            _, was_fetched = ingest_scan_symbol(conn, symbol)
            fetched += was_fetched
            cache_hits += not was_fetched
            conn.commit()
        except httpx.HTTPStatusError as e:
            conn.rollback()
            if e.response.status_code == 429:
                skipped = tickers[i:]
                gaps += [(s, "SKIPPED (rate limit)") for s in skipped]
                notify("Momentum scan rate-limited",
                       f"Tiingo 429 at {symbol}; {len(skipped)} pockets skipped"
                       " — partial report tonight",
                       priority="high", tags="warning")
                break
            gaps.append((symbol, f"fetch failed (HTTP {e.response.status_code})"))
        except Exception as e:  # noqa: BLE001 — one bad ticker must not cost the scan
            conn.rollback()
            gaps.append((symbol, f"fetch failed ({type(e).__name__})"))
    return fetched, cache_hits, gaps


def _closes(bars, cutoff: date) -> list[Decimal]:
    return [b.adj_close for b in sorted(bars, key=lambda b: b.session)
            if b.session <= cutoff]


def _theme_rows(conn, session: date, horizons, gaps):
    """Accepted themes' symbols, scored in one pooled single-name
    cross-section (never against the ETFs — vol regimes differ), read from
    price_bars (the engine-adopted, pin-respecting series)."""
    theme_symbols = conn.execute(
        "SELECT t.slug, ts.symbol FROM themes t"
        " JOIN theme_symbols ts ON ts.theme_id = t.id"
        " WHERE t.status = 'ACCEPTED' ORDER BY t.slug, ts.symbol",
    ).fetchall()
    closes: dict[str, list[Decimal]] = {}
    for _, symbol in theme_symbols:
        if symbol in closes:
            continue
        rows = conn.execute(
            "SELECT session_date, adj_close FROM price_bars WHERE symbol = %s"
            " AND session_date <= %s ORDER BY session_date", (symbol, session),
        ).fetchall()
        if not rows or rows[-1][0] != session:
            gaps.append((symbol, "STALE (no price_bars row for session)"))
            continue
        closes[symbol] = [r[1] for r in rows]
    scores = scan_scores(closes, horizons)
    sectors = wikipedia.sector_map(conn)
    sections = []
    for slug in dict.fromkeys(s for s, _ in theme_symbols):
        rows = tuple(
            ThemeRow(symbol=sym,
                     gics_sector=sectors.get(sym, (None, None))[0],
                     gics_sub_industry=sectors.get(sym, (None, None))[1],
                     score=scores[sym])
            for t_slug, sym in theme_symbols if t_slug == slug and sym in scores
        )
        if rows:
            sections.append((slug, rows))
    return tuple(sections)


def _hot_pocket_members(conn, pockets: list[PocketRow]) -> tuple:
    """S&P 500 members of the top non-sector pockets' configured GICS
    sub-industries — context lines only."""
    by_ticker = {p["ticker"]: p for p in SCAN_CFG.get("pockets", [])}
    out = []
    hot = [p for p in pockets if p.kind != "sector"][:5]
    for p in hot:
        subs = by_ticker.get(p.ticker, {}).get("gics_sub_industries")
        if not subs:
            continue
        rows = conn.execute(
            "SELECT ticker, security_name FROM sp500_constituents"
            " WHERE gics_sub_industry = ANY(%s) ORDER BY gics_sub_industry, ticker",
            (list(subs),),
        ).fetchall()
        if rows:
            out.append((f"{p.name} ({p.ticker})",
                        tuple(f"{t} — {n}" if n else t for t, n in rows)))
    return tuple(out)


def _pocket_members(sectors: dict[str, tuple[str, str | None]]) -> dict[str, list[str]]:
    """pocket name → sorted constituent tickers, from GICS: sector pockets
    match by gics_sector (pocket names mirror GICS sector names); industry/
    theme pockets match their configured gics_sub_industries."""
    by_sector: dict[str, list[str]] = {}
    by_sub: dict[str, list[str]] = {}
    for ticker, (sector, sub) in sectors.items():
        by_sector.setdefault(sector, []).append(ticker)
        if sub:
            by_sub.setdefault(sub, []).append(ticker)
    out: dict[str, list[str]] = {}
    for p in SCAN_CFG.get("pockets", []):
        if p["kind"] == "sector":
            members = by_sector.get(p.get("gics_sector", p["name"]), [])
        else:
            members = [t for s in p.get("gics_sub_industries", [])
                       for t in by_sub.get(s, [])]
        if members:
            out[p["name"]] = sorted(set(members))
    return out


def _referee(conn, session: date) -> tuple[str, ...]:
    """Tiingo-vs-Alpaca adjusted-close referee over the engine-critical
    overlap (theme universe + benchmarks). Report notes + one classified
    VENDOR_MISMATCH incident per newly-affected symbol; never mutates
    price_bars. Any failure degrades to a note."""
    from data.reconcile import raise_incident, referee_universe
    from schemas.enums import IncidentClass

    cfg = SCAN_CFG.get("referee", {})
    if not cfg.get("enabled"):
        return ()
    try:
        mismatches = referee_universe(
            conn, session,
            symbols=sorted(_nightly_symbols(conn)),
            window_sessions=int(cfg.get("window_sessions", 21)),
            rel_tolerance=Decimal(str(cfg.get("rel_tolerance_pct", "0.5"))) / 100,
            min_overlap=int(cfg.get("min_overlap", 10)),
        )
        if not mismatches:
            return ("no Tiingo-vs-Alpaca divergence beyond tolerance",)
        notes = []
        by_symbol: dict[str, list] = {}
        for m in mismatches:
            by_symbol.setdefault(m.symbol, []).append(m)
        for symbol, rows in sorted(by_symbol.items()):
            worst = max(rows, key=lambda m: m.rel_diff)
            note = (f"{symbol}: {len(rows)} session(s) diverge >"
                    f"{cfg.get('rel_tolerance_pct', '0.5')}% (worst"
                    f" {float(worst.rel_diff) * 100:.2f}% on {worst.session}:"
                    f" tiingo {worst.reference_close} vs alpaca {worst.candidate_close})")
            notes.append(note)
            already_open = conn.execute(
                "SELECT 1 FROM data_incidents WHERE incident_class = 'VENDOR_MISMATCH'"
                " AND symbol = %s AND status = 'OPEN' LIMIT 1", (symbol,),
            ).fetchone()
            if not already_open:
                raise_incident(
                    conn, incident_class=IncidentClass.VENDOR_MISMATCH,
                    source_name="alpaca_scan", symbol=symbol, description=note)
        conn.commit()
        notify("Cross-vendor price mismatch",
               "; ".join(notes)[:400], priority="default", tags="warning")
        return tuple(notes)
    except Exception as e:  # noqa: BLE001 — the referee must not cost the scan
        conn.rollback()
        return (f"referee unavailable: {type(e).__name__}: {str(e)[:150]}",)


def _single_name_phase(conn, session: date, horizons):
    """Alpaca ingest + pocket internals + referee. Fully best-effort: any
    failure degrades to an internals_note; the ETF scan is never touched.
    Returns (internals, index_internals, internals_note, referee_notes,
    alpaca_closes) — alpaca_closes feeds the price_source flip."""
    from data.adapters import alpaca

    alpaca_cfg = CONFIG.get("alpaca", {})
    if not alpaca_cfg.get("enabled") or not SCAN_CFG.get("include_sp500_members"):
        return (), None, None, (), {}
    try:
        constituents = wikipedia.sector_map(conn)
        if not constituents:
            return (), None, "constituents snapshot empty — internals off", (), {}
        etf_tickers = [p["ticker"] for p in SCAN_CFG.get("pockets", [])]
        universe = sorted(set(constituents) | set(etf_tickers))
        adjustments: tuple[str, ...] = ("all",)
        if session.weekday() == int(alpaca_cfg.get("raw_pass_session_weekday", 4)):
            adjustments = ("all", "raw")
        fetched, hits, gaps = alpaca.ingest_scan_universe(
            conn, universe, session=session,
            history_days=int(alpaca_cfg.get("history_days", 130)),
            chunk_size=int(alpaca_cfg.get("chunk_size", 100)),
            max_requests=int(alpaca_cfg.get("max_requests_per_run", 40)),
            adjustments=adjustments,
            feed=alpaca_cfg.get("feed", "sip"),
        )
        closes, scan_session = alpaca.latest_scan_closes(conn, cutoff=session)
        bars, _ = alpaca.latest_scan_bars(conn, adjustment="all")
        fresh = {s: series for s, series in closes.items()
                 if bars.get(s) and bars[s][-1].session >= session}
        stale = len(closes) - len(fresh)
        single_names = {s: v for s, v in fresh.items() if s in constituents}
        if not single_names:
            note = (f"Alpaca data covers no fresh single names for {session}"
                    f" (ingest gaps: {len(gaps)})")
            return (), None, note, (), {}
        icfg = SCAN_CFG.get("internals", {})
        kwargs = dict(
            dispersion_horizon_index=int(icfg.get("dispersion_horizon_index", 1)),
            avg_window=int(icfg.get("avg_window_sessions", 50)),
            extreme_window=int(icfg.get("extreme_window_sessions", 63)),
        )
        scores = member_composites(single_names, horizons)
        members = _pocket_members(wikipedia.sector_map(conn))
        internals = [
            pocket_internals(
                name, {s: single_names[s] for s in syms if s in single_names},
                scores, **kwargs)
            for name, syms in members.items()
        ]
        internals = [i for i in internals if i.members_total]
        internals.sort(key=lambda i: (i.median_member_composite is None,
                                      -(i.median_member_composite or 0), i.pocket))
        index_row = index_breadth(single_names, scores, **kwargs)
        note_bits = [f"alpaca pages fetched {fetched}, cache-hit {hits}"]
        if gaps:
            note_bits.append(f"{len(gaps)} chunk-symbol gaps")
        if stale:
            note_bits.append(f"{stale} stale symbols excluded")
        etf_bars = {s: bars[s] for s in etf_tickers if s in fresh}
        return (tuple(internals), index_row, "; ".join(note_bits),
                _referee(conn, session), etf_bars)
    except alpaca.AlpacaDark as e:
        return (), None, f"Alpaca dark: {e}", (), {}
    except Exception as e:  # noqa: BLE001 — internals must not cost the scan
        conn.rollback()
        return (), None, f"internals failed: {type(e).__name__}: {str(e)[:150]}", (), {}


def _narrate(conn, data: ScanReportData) -> tuple[str | None, str | None]:
    """Best-effort guarded narration; any failure is a note, never a halt."""
    if not data.pockets:
        return None, "no pockets scored"
    try:
        from agents.roles.reporter import _known_symbols
        from agents.roles.scan_narrator import narrate_scan

        exp_row = conn.execute(
            "SELECT id FROM experiments WHERE name = %s",
            (CONFIG["stage3"]["live_experiment"],),
        ).fetchone()
        if exp_row is None:
            return None, "live experiment not found"
        context_text, values, allowed = narrator_inputs(data)
        return narrate_scan(
            conn,
            experiment_id=exp_row[0],
            context_text=context_text,
            values=values,
            allowed_symbols=allowed,
            known_symbols=_known_symbols(conn) | allowed,
            model_tag=SCAN_CFG.get("narrator_model") or None,
        )
    except Exception as e:  # noqa: BLE001 — narration must not cost the scan
        conn.rollback()
        return None, f"{type(e).__name__}: {str(e)[:200]}"


@loud("momentum_scan")
def main(as_of_override: str | None = None) -> None:
    if not SCAN_CFG.get("enabled"):
        print("scan disabled in config — nothing to do")
        return
    now = datetime.now(timezone.utc)
    session = (
        date.fromisoformat(as_of_override) if as_of_override
        else latest_closed_session(now)
    )
    if session is None:
        print("no closed session in the last week — nothing to do")
        return
    horizons = tuple(SCAN_CFG.get("horizons_sessions", [5, 21, 63]))
    pockets_cfg = SCAN_CFG.get("pockets", [])

    with connect() as conn:
        cycle = claim_scan_cycle(conn, session.isoformat(), "timer")
        if cycle is None:
            print(f"scan cycle for {session} already COMPLETE — idempotent no-op")
            return
        phase = "start"
        try:
            phase = "constituents"
            gaps: list[tuple[str, str]] = []
            note = _refresh_constituents(conn)
            if note:
                gaps.append(("sp500_constituents", note))

            phase = "ingest"
            nightly = _nightly_symbols(conn)
            use_alpaca_etfs = SCAN_CFG.get("price_source") == "alpaca"
            to_fetch = ([] if use_alpaca_etfs else
                        [p["ticker"] for p in pockets_cfg
                         if p["ticker"] not in nightly])
            fetched, cache_hits, ingest_gaps = _ingest_etfs(conn, to_fetch)
            # An ingest failure only matters if the cached documents can't
            # cover the session — a 429'd symbol whose earlier fetch already
            # holds tonight's bar still scores (degrade from cache, not to
            # nothing). The compute pass below decides the real gaps.
            ingest_notes = dict(ingest_gaps)

            phase = "singles"
            internals, index_row, internals_note, referee_notes, alpaca_etf_bars = (
                _single_name_phase(conn, session, horizons))

            phase = "compute"
            prior = prior_scan_session(session)
            closes: dict[str, list[Decimal]] = {}
            prior_closes: dict[str, list[Decimal]] = {}
            for p in pockets_cfg:
                ticker = p["ticker"]
                if use_alpaca_etfs:
                    abars = alpaca_etf_bars.get(ticker)
                    if not abars or abars[-1].session < session:
                        gaps.append((ticker, "STALE (alpaca: no bar for session)"))
                        continue
                    closes[ticker] = [b.close for b in abars if b.session <= session]
                    if prior:
                        prior_closes[ticker] = [b.close for b in abars
                                                if b.session <= prior]
                    continue
                bars = latest_scan_bars(conn, ticker)
                if not bars or max(b.session for b in bars) < session:
                    gaps.append((ticker, ingest_notes.get(
                        ticker, "STALE (no bar for session)")))
                    continue
                closes[ticker] = _closes(bars, session)
                if prior:
                    prior_closes[ticker] = _closes(bars, prior)
            etf_gapped = [g for g in gaps if g[0] in {p["ticker"] for p in pockets_cfg}]
            if pockets_cfg and len(etf_gapped) > len(pockets_cfg) * 0.25:
                notify("Momentum scan degraded",
                       f"{len(etf_gapped)}/{len(pockets_cfg)} pockets have no data tonight",
                       priority="default", tags="warning")
            if not closes:
                # Every pocket is stale — the session's EOD isn't final yet
                # (e.g. a manual run before Tiingo posts) or ingest collapsed.
                # FAILED, not degraded-COMPLETE: the claim must stay
                # reclaimable so the timer's re-run produces a real report.
                raise RuntimeError(
                    f"no scan pocket has a final bar for {session} — "
                    "Tiingo EOD not final; halting (re-run later)"
                )
            scores = scan_scores(closes, horizons)
            ranked = rank_by_composite(scores)
            deltas = (rank_deltas(ranked, rank_by_composite(
                scan_scores(prior_closes, horizons))) if prior else {})
            by_ticker = {p["ticker"]: p for p in pockets_cfg}
            pocket_rows = [
                PocketRow(ticker=t, name=by_ticker[t]["name"],
                          kind=by_ticker[t]["kind"], score=scores[t],
                          delta=deltas.get(t))
                for t in ranked
            ]
            unscored = tuple(sorted(t for t, s in scores.items()
                                    if s.composite is None))
            theme_sections = _theme_rows(conn, session, horizons, gaps)

            data = ScanReportData(
                session=session, horizons=horizons,
                pockets=tuple(pocket_rows), unscored=unscored,
                theme_sections=theme_sections,
                hot_pocket_members=_hot_pocket_members(conn, pocket_rows),
                gaps=tuple(gaps), prior_session=prior,
                fetched=fetched, cache_hits=cache_hits,
                ingest_skipped=len(ingest_notes),
                snapshot_refreshed=wikipedia.snapshot_refreshed_at(conn),
                internals=internals, index_internals=index_row,
                internals_note=internals_note, referee_notes=referee_notes,
            )

            phase = "narrate"
            narration, narration_note = _narrate(conn, data)
            data = replace(data, narration=narration,
                           narration_note=narration_note)

            phase = "render"
            path = write_scan_report(render_scan(data), session)
            print(f"momentum scan {session}: {len(pocket_rows)} pockets ranked,"
                  f" {len(gaps)} gaps → {path}")

            finish_scan_cycle(
                conn, cycle, "COMPLETE",
                f"render (degraded:{len(gaps)})" if gaps else "render")
        except Exception as e:
            conn.rollback()  # the failing statement may have aborted the txn
            finish_scan_cycle(conn, cycle, "FAILED", phase,
                              f"{type(e).__name__}: {str(e)[:1500]}")
            raise


if __name__ == "__main__":
    main(sys.argv[1].removeprefix("--as-of=") if len(sys.argv) > 1 else None)
