"""Schwab Trader API market-data adapter. Honors contracts/schwab.yaml
(two-vendor architecture Phase B, DECISIONS.md 2026-07-30):
- operator/research layers ONLY: never price_bars, never the engine, never
  assertions, never LLM analyst/scout context
- pricehistory candles are split-adjusted only (accepted caveat, standing
  report footnote); Tiingo remains the engine's price truth
- every response cached immutably; Bearer token in headers, never persisted
- SchwabDark (auth) propagates so callers render dark sections; everything
  else degrades per-symbol skip-and-note

    uv run python -m data.adapters.schwab quotes SPY $SPX $VIX   # smoke
"""

import json
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from data import repository as repo
from data.adapters.core import fetch_and_cache
from data.adapters.schwab_auth import SchwabDark, access_token
from data.db import connect
from schemas.models import Document

LICENSE_NOTE = ("Schwab Trader API: personal use, no redistribution, local"
                " caching only (contracts/schwab.yaml)")
BASE = "https://api.schwabapi.com/marketdata/v1"


@dataclass(frozen=True)
class Candle:
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class QuoteSnap:
    symbol: str
    last: Decimal | None
    net_pct_change: Decimal | None
    close: Decimal | None
    high_52wk: Decimal | None
    low_52wk: Decimal | None
    total_volume: int | None
    description: str | None


@dataclass(frozen=True)
class MoverRow:
    symbol: str
    description: str | None
    last: Decimal | None
    net_pct_change: Decimal | None
    volume: int | None


def _fetch(conn, *, url: str, params: dict, source_name: str,
           extra_stored: dict | None = None, timeout: float = 30.0):
    """Authenticated cached GET. The Bearer header is never persisted."""
    token = access_token()
    return fetch_and_cache(
        conn, url=url, params=params,
        stored_params={**{k: str(v) for k, v in params.items()},
                       **(extra_stored or {})},
        source_name=source_name, license_note=LICENSE_NOTE,
        first_eligible_decision_time=datetime.now(timezone.utc),
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )


# --------------------------------------------------------------------------- #
# Price history (schwab_history) — one symbol per call
# --------------------------------------------------------------------------- #

def _epoch_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp() * 1000)


def ingest_daily_history(conn, symbol: str, *, start: date, end: date,
                         session_tag: str) -> tuple[Document, bool]:
    params = {
        "symbol": symbol, "periodType": "year", "frequencyType": "daily",
        "frequency": 1, "startDate": _epoch_ms(start),
        "endDate": _epoch_ms(end + timedelta(days=1)),
    }
    return _fetch(conn, url=f"{BASE}/pricehistory", params=params,
                  source_name="schwab_history",
                  extra_stored={"scan_session": session_tag})


def ingest_singles(
    conn, symbols: list[str], *, session: date, history_days: int,
    throttle_rps: float, max_requests: int,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Nightly single-name candles for the scan universe. Per-symbol
    containment; hard request cap; 429 stops the loop (contract: never
    retried that night); SchwabDark propagates (dark rendering)."""
    import httpx

    start = session - timedelta(days=history_days)
    pause = 1.0 / throttle_rps if throttle_rps > 0 else 0
    fetched = hits = 0
    gaps: list[tuple[str, str]] = []
    for i, symbol in enumerate(sorted(set(symbols))):
        if i >= max_requests:
            gaps += [(s, "SKIPPED (request cap)") for s in sorted(set(symbols))[i:]]
            break
        try:
            _, was = ingest_daily_history(
                conn, symbol, start=start, end=session, session_tag=str(session))
            fetched += was
            hits += not was
            conn.commit()
        except SchwabDark:
            raise
        except httpx.HTTPStatusError as e:
            conn.rollback()
            if e.response.status_code == 429:
                gaps += [(s, "SKIPPED (rate limit)")
                         for s in sorted(set(symbols))[i:]]
                break
            gaps.append((symbol, f"fetch failed (HTTP {e.response.status_code})"))
        except Exception as e:  # noqa: BLE001 — one symbol must not cost the scan
            conn.rollback()
            gaps.append((symbol, f"fetch failed ({type(e).__name__})"))
        if pause:
            time.sleep(pause)
    return fetched, hits, gaps


def parse_candles(doc: Document) -> list[Candle]:
    """Derive-on-read; malformed candles are skipped (absence, not repair)."""
    payload = json.loads(doc.raw_bytes)
    out = []
    for c in payload.get("candles") or []:
        try:
            out.append(Candle(
                session=datetime.fromtimestamp(
                    c["datetime"] / 1000, tz=timezone.utc).date(),
                open=Decimal(str(c["open"])), high=Decimal(str(c["high"])),
                low=Decimal(str(c["low"])), close=Decimal(str(c["close"])),
                volume=int(c.get("volume") or 0),
            ))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return sorted(out, key=lambda c: c.session)


def latest_single_closes(conn, *, cutoff: date) -> tuple[dict[str, list[Decimal]], date | None]:
    """Ascending close series per symbol from the newest nightly batch (max
    scan_session tag). Pure derive-on-read."""
    docs = repo.fetch(
        conn, Document,
        "source_name = 'schwab_history' AND query_params ? 'scan_session'")
    if not docs:
        return {}, None
    newest = max(d.query_params["scan_session"] for d in docs)
    out: dict[str, list[Decimal]] = {}
    for d in docs:
        if d.query_params.get("scan_session") != newest:
            continue
        symbol = d.query_params.get("symbol", "").upper()
        candles = parse_candles(d)
        if symbol and candles:
            out[symbol] = [c.close for c in candles if c.session <= cutoff]
    return out, date.fromisoformat(newest)


def latest_single_last_sessions(conn) -> dict[str, date]:
    """symbol → last candle session in the newest batch (staleness checks)."""
    docs = repo.fetch(
        conn, Document,
        "source_name = 'schwab_history' AND query_params ? 'scan_session'")
    if not docs:
        return {}
    newest = max(d.query_params["scan_session"] for d in docs)
    out: dict[str, date] = {}
    for d in docs:
        if d.query_params.get("scan_session") != newest:
            continue
        candles = parse_candles(d)
        if candles:
            out[d.query_params.get("symbol", "").upper()] = candles[-1].session
    return out


def ingest_index_history(conn, symbol: str, *, period_years: int,
                         max_age_days: int) -> tuple[Document, bool] | None:
    """Weekly-refreshed long history for index/vol series (e.g. $VIX
    percentile distribution). Freshness-gated on the newest cached doc."""
    existing = repo.fetch(
        conn, Document,
        "source_name = 'schwab_history' AND query_params->>'symbol' = %s"
        " AND query_params->>'periodType' = 'year'"
        " AND query_params ? 'period'",
        (symbol,))
    now = datetime.now(timezone.utc)
    if existing and max(d.ingestion_time for d in existing) > now - timedelta(days=max_age_days):
        return None
    params = {"symbol": symbol, "periodType": "year", "period": period_years,
              "frequencyType": "daily", "frequency": 1}
    return _fetch(conn, url=f"{BASE}/pricehistory", params=params,
                  source_name="schwab_history", timeout=60.0)


def latest_index_history(conn, symbol: str) -> list[Candle]:
    docs = repo.fetch(
        conn, Document,
        "source_name = 'schwab_history' AND query_params->>'symbol' = %s"
        " AND query_params ? 'period'",
        (symbol,))
    if not docs:
        return []
    return parse_candles(max(docs, key=lambda d: d.ingestion_time))


# --------------------------------------------------------------------------- #
# Batch quotes (schwab_quotes) + movers (schwab_movers)
# --------------------------------------------------------------------------- #

def ingest_quotes(conn, symbols: list[str], *, session_tag: str,
                  kind: str) -> tuple[Document, bool]:
    """One batch snapshot; `kind` (tape|theme) keeps distinct nightly
    fetches from hash-colliding into confusing groupings."""
    params = {"symbols": ",".join(sorted(set(symbols))), "indicative": "false"}
    return _fetch(conn, url=f"{BASE}/quotes", params=params,
                  source_name="schwab_quotes",
                  extra_stored={"scan_session": session_tag, "kind": kind})


def _dec(value) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except (ValueError, ArithmeticError):
        return None


def parse_quotes(doc: Document) -> dict[str, QuoteSnap]:
    payload = json.loads(doc.raw_bytes)
    out: dict[str, QuoteSnap] = {}
    for symbol, node in payload.items():
        if not isinstance(node, dict) or "quote" not in node:
            continue  # skips the "errors" node and malformed entries
        q = node.get("quote") or {}
        ref = node.get("reference") or {}
        out[symbol.upper()] = QuoteSnap(
            symbol=symbol.upper(),
            last=_dec(q.get("lastPrice")),
            net_pct_change=_dec(q.get("netPercentChange")),
            close=_dec(q.get("closePrice")),
            high_52wk=_dec(q.get("52WeekHigh")),
            low_52wk=_dec(q.get("52WeekLow")),
            total_volume=int(q["totalVolume"]) if q.get("totalVolume") is not None else None,
            description=ref.get("description"),
        )
    return out


def latest_quotes(conn, *, kind: str) -> tuple[dict[str, QuoteSnap], str | None]:
    docs = repo.fetch(
        conn, Document,
        "source_name = 'schwab_quotes' AND query_params->>'kind' = %s", (kind,))
    if not docs:
        return {}, None
    newest = max(docs, key=lambda d: d.ingestion_time)
    return parse_quotes(newest), (newest.query_params or {}).get("scan_session")


def ingest_movers(conn, index: str, *, sort: str, session_tag: str) -> tuple[Document, bool]:
    params = {"sort": sort}
    return _fetch(conn, url=f"{BASE}/movers/{index}", params=params,
                  source_name="schwab_movers",
                  extra_stored={"scan_session": session_tag, "index": index})


def parse_movers(doc: Document) -> list[MoverRow]:
    payload = json.loads(doc.raw_bytes)
    out = []
    for row in payload.get("screeners") or []:
        try:
            out.append(MoverRow(
                symbol=str(row.get("symbol", "")).upper(),
                description=row.get("description"),
                last=_dec(row.get("lastPrice")),
                net_pct_change=_dec(row.get("netPercentChange")
                                    if row.get("netPercentChange") is not None
                                    else row.get("netChange")),
                volume=int(row["volume"]) if row.get("volume") is not None
                else (int(row["totalVolume"]) if row.get("totalVolume") is not None else None),
            ))
        except (TypeError, ValueError):
            continue
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "quotes"
    symbols = sys.argv[2:] or ["SPY"]
    with connect() as conn:
        if cmd == "quotes":
            doc, was = ingest_quotes(conn, symbols,
                                     session_tag=str(datetime.now(timezone.utc).date()),
                                     kind="smoke")
            conn.commit()
            for s, q in parse_quotes(doc).items():
                print(f"{s}: last={q.last} 52wk=[{q.low_52wk}, {q.high_52wk}]")
        elif cmd == "history":
            today = datetime.now(timezone.utc).date()
            doc, was = ingest_daily_history(
                conn, symbols[0], start=today - timedelta(days=30), end=today,
                session_tag=str(today))
            conn.commit()
            candles = parse_candles(doc)
            print(f"{symbols[0]}: {len(candles)} candles, last {candles[-1] if candles else None}")
