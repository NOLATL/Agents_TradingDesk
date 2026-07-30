"""Alpaca Market Data adapter (Basic tier). Honors contracts/alpaca.yaml
(three-vendor architecture, DECISIONS.md 2026-07-30, Phase A):
- momentum-scan price engine ONLY (source_name='alpaca_scan'): never enters
  price_bars, the engine, assertions, or any LLM analyst/scout context
- multi-symbol daily bars; adjustment explicit per request (all nightly,
  raw weekly); full-window refetch absorbs adjustment restatements
- deterministic sorted chunking so stored_params are stable; API keys live
  in headers, which fetch_and_cache never persists
- a symbol absent from a response is an absence, never filled

    uv run python -m data.adapters.alpaca SPY QQQ   # ad-hoc smoke fetch
"""

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from dotenv import load_dotenv

from data import repository as repo
from data.adapters.core import fetch_and_cache
from data.db import connect
from schemas.models import Document

load_dotenv()

LICENSE_NOTE = ("Alpaca Basic: personal use, local caching, no redistribution"
                " (contracts/alpaca.yaml)")
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
PAGE_LIMIT = 10000


class AlpacaDark(Exception):
    """Missing credentials or auth failure — the scan degrades to the Tiingo
    ETF-pocket view (contract fallback), never halts."""


@dataclass(frozen=True)
class AlpacaBar:
    symbol: str
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


def _headers() -> dict[str, str]:
    key, secret = os.environ.get("ALPACA_API_KEY_ID"), os.environ.get("ALPACA_API_SECRET_KEY")
    if not key or not secret:
        raise AlpacaDark("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def chunk_symbols(symbols: list[str], chunk_size: int) -> list[list[str]]:
    """Deterministic chunking: sorted, deduped, fixed-size — stable
    stored_params across nights so document grouping is by content."""
    ordered = sorted(set(s.upper() for s in symbols))
    return [ordered[i:i + chunk_size] for i in range(0, len(ordered), chunk_size)]


def ingest_scan_chunk(
    conn, symbols: list[str], *, start: date, session: date,
    adjustment: str, feed: str,
) -> list[tuple[Document, bool]]:
    """All pages for one symbol chunk. Each page is its own immutable
    document; pages of one nightly batch share stored_params scan_session,
    which is how latest_scan_bars finds the newest complete fetch."""
    headers = _headers()
    docs: list[tuple[Document, bool]] = []
    page_token: str | None = None
    while True:
        params = {
            "symbols": ",".join(symbols), "timeframe": "1Day",
            "start": str(start), "adjustment": adjustment,
            "feed": feed, "limit": PAGE_LIMIT, "sort": "asc",
        }
        if page_token:
            params["page_token"] = page_token
        doc, fetched = fetch_and_cache(
            conn,
            url=BARS_URL,
            params=params,
            stored_params={**{k: str(v) for k, v in params.items()},
                           "scan_session": str(session)},
            source_name="alpaca_scan",
            license_note=LICENSE_NOTE,
            first_eligible_decision_time=datetime.now(timezone.utc),
            headers=headers,
            timeout=60.0,
        )
        docs.append((doc, fetched))
        page_token = json.loads(doc.raw_bytes).get("next_page_token")
        if not page_token:
            return docs


def ingest_scan_universe(
    conn, symbols: list[str], *, session: date, history_days: int,
    chunk_size: int, max_requests: int, adjustments: tuple[str, ...],
    feed: str = "sip",
) -> tuple[int, int, list[tuple[str, str]]]:
    """(fetched, cache_hits, gaps). Per-chunk commit containment; request cap
    and 429 skip-and-note per contract. A failed chunk gaps all its symbols."""
    import httpx

    start = session - timedelta(days=history_days)
    fetched = cache_hits = requests_made = 0
    gaps: list[tuple[str, str]] = []
    chunks = chunk_symbols(symbols, chunk_size)
    for adjustment in adjustments:
        for chunk in chunks:
            if requests_made >= max_requests:
                gaps += [(s, f"SKIPPED (request cap, {adjustment})") for s in chunk]
                continue
            try:
                docs = ingest_scan_chunk(
                    conn, chunk, start=start, session=session,
                    adjustment=adjustment, feed=feed)
                requests_made += len(docs)
                fetched += sum(1 for _, was in docs if was)
                cache_hits += sum(1 for _, was in docs if not was)
                conn.commit()
            except AlpacaDark:
                raise
            except httpx.HTTPStatusError as e:
                conn.rollback()
                if e.response.status_code in (401, 403):
                    raise AlpacaDark(f"auth rejected (HTTP {e.response.status_code})") from e
                if e.response.status_code == 429:
                    gaps += [(s, f"SKIPPED (rate limit, {adjustment})") for s in chunk]
                    continue
                gaps += [(s, f"fetch failed (HTTP {e.response.status_code})") for s in chunk]
            except Exception as e:  # noqa: BLE001 — one chunk must not cost the scan
                conn.rollback()
                gaps += [(s, f"fetch failed ({type(e).__name__})") for s in chunk]
    return fetched, cache_hits, gaps


def parse_bars(doc: Document) -> dict[str, list[AlpacaBar]]:
    """Derive-on-read; malformed bars are skipped (absence, not repair)."""
    payload = json.loads(doc.raw_bytes)
    out: dict[str, list[AlpacaBar]] = {}
    for symbol, bars in (payload.get("bars") or {}).items():
        rows = []
        for b in bars or []:
            try:
                rows.append(AlpacaBar(
                    symbol=symbol.upper(),
                    session=date.fromisoformat(b["t"][:10]),
                    open=Decimal(str(b["o"])),
                    high=Decimal(str(b["h"])),
                    low=Decimal(str(b["l"])),
                    close=Decimal(str(b["c"])),
                    volume=int(b.get("v") or 0),
                ))
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
        if rows:
            out[symbol.upper()] = rows
    return out


def latest_scan_bars(
    conn, *, adjustment: str, max_age_sessions_key: str = "scan_session",
) -> tuple[dict[str, list[AlpacaBar]], date | None]:
    """Bars from the newest complete nightly fetch (all pages sharing the max
    scan_session tag for the given adjustment). Returns ({symbol: bars},
    scan_session or None). Pure derive-on-read — no mutable state."""
    docs = repo.fetch(
        conn, Document,
        "source_name = 'alpaca_scan' AND query_params->>'adjustment' = %s",
        (adjustment,),
    )
    if not docs:
        return {}, None
    newest = max(d.query_params[max_age_sessions_key] for d in docs
                 if d.query_params and max_age_sessions_key in d.query_params)
    merged: dict[str, list[AlpacaBar]] = {}
    for d in docs:
        if (d.query_params or {}).get(max_age_sessions_key) != newest:
            continue
        for symbol, bars in parse_bars(d).items():
            merged.setdefault(symbol, []).extend(bars)
    for symbol in merged:
        merged[symbol] = sorted(merged[symbol], key=lambda b: b.session)
    return merged, date.fromisoformat(newest)


def latest_scan_closes(
    conn, *, cutoff: date, adjustment: str = "all",
) -> tuple[dict[str, list[Decimal]], date | None]:
    """Ascending adjusted-close series per symbol, truncated at cutoff."""
    bars, scan_session = latest_scan_bars(conn, adjustment=adjustment)
    return (
        {sym: [b.close for b in rows if b.session <= cutoff]
         for sym, rows in bars.items()},
        scan_session,
    )


if __name__ == "__main__":
    symbols = [s.upper() for s in sys.argv[1:]] or ["SPY"]
    today = datetime.now(timezone.utc).date()
    with connect() as conn:
        result = ingest_scan_universe(
            conn, symbols, session=today, history_days=130, chunk_size=100,
            max_requests=10, adjustments=("all",))
    print(f"fetched={result[0]} cache_hits={result[1]} gaps={result[2]}")
