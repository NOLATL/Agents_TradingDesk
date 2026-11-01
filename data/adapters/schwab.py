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
import re
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


def ingest_tagged_history(conn, symbol: str, *, period_years: int,
                          purpose: str) -> tuple[Document, bool]:
    """Unconditional daily-candle history fetch tagged with a `purpose`
    stored param, so an operator consumer (e.g. the AI-bubble monitor,
    2026-11-01) reads back only its own documents and never collides with
    the scan's freshness-gated `period` histories."""
    params = {"symbol": symbol, "periodType": "year", "period": period_years,
              "frequencyType": "daily", "frequency": 1}
    return _fetch(conn, url=f"{BASE}/pricehistory", params=params,
                  source_name="schwab_history", extra_stored={"purpose": purpose},
                  timeout=60.0)


def latest_tagged_history(conn, symbol: str, *, purpose: str) -> list[Candle]:
    """Newest tagged history document only (LIMIT 1 — never loads the
    accumulated archive)."""
    docs = repo.fetch(
        conn, Document,
        "source_name = 'schwab_history' AND query_params->>'symbol' = %s"
        " AND query_params->>'purpose' = %s ORDER BY ingestion_time DESC LIMIT 1",
        (symbol, purpose))
    return parse_candles(docs[0]) if docs else []


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


@dataclass(frozen=True)
class FundamentalsSnap:
    symbol: str
    pe: Decimal | None
    pb: Decimal | None
    peg: Decimal | None
    div_yield: Decimal | None
    eps_ttm: Decimal | None
    market_cap: Decimal | None
    beta: Decimal | None
    short_int_pct_float: Decimal | None
    short_int_days_to_cover: Decimal | None
    high_52wk: Decimal | None
    low_52wk: Decimal | None
    # Panel fields (2026-08-07): always present in the cached payloads,
    # parsed late — derive-on-read means old documents light up too.
    ps: Decimal | None = None                   # prRatio (price/revenue)
    pcf: Decimal | None = None                  # pcfRatio (price/cash flow)
    book_value_per_share: Decimal | None = None
    gross_margin_ttm: Decimal | None = None     # percent points
    operating_margin_ttm: Decimal | None = None
    net_margin_ttm: Decimal | None = None
    roe: Decimal | None = None                  # percent points
    roa: Decimal | None = None
    roi: Decimal | None = None
    current_ratio: Decimal | None = None
    quick_ratio: Decimal | None = None
    lt_debt_to_equity: Decimal | None = None    # vendor percent (135 = 1.35x)
    total_debt_to_equity: Decimal | None = None
    rev_change_ttm: Decimal | None = None       # percent points YoY
    rev_change_year: Decimal | None = None
    eps_change_ttm: Decimal | None = None
    div_amount: Decimal | None = None           # forward annual $/share
    div_pay_amount: Decimal | None = None       # per-payment $/share
    div_freq: int | None = None                 # payments per year
    next_div_ex_date: str | None = None         # ISO date
    next_div_pay_date: str | None = None
    shares_outstanding: Decimal | None = None
    avg_vol_3mo: Decimal | None = None


def ingest_fundamentals(conn, symbols: list[str], *, session_tag: str,
                        chunk_size: int = 50) -> list[tuple[Document, bool]]:
    """instruments?projection=fundamental. Batch-ability is unconfirmed on
    the vendor side: chunks are tried batched and fall back to per-symbol
    on HTTP 400 (per-symbol at 120/min is fine for Friday volumes)."""
    import httpx

    docs: list[tuple[Document, bool]] = []
    ordered = sorted(set(s.upper() for s in symbols))
    for i in range(0, len(ordered), chunk_size):
        chunk = ordered[i:i + chunk_size]
        try:
            docs.append(_fetch(
                conn, url=f"{BASE}/instruments",
                params={"symbol": ",".join(chunk), "projection": "fundamental"},
                source_name="schwab_fundamentals",
                extra_stored={"scan_session": session_tag}))
            conn.commit()
        except httpx.HTTPStatusError as e:
            conn.rollback()
            if e.response.status_code != 400 or len(chunk) == 1:
                raise
            for symbol in chunk:  # batch rejected → per-symbol fallback
                docs.append(_fetch(
                    conn, url=f"{BASE}/instruments",
                    params={"symbol": symbol, "projection": "fundamental"},
                    source_name="schwab_fundamentals",
                    extra_stored={"scan_session": session_tag}))
                conn.commit()
                time.sleep(0.5)  # 120/min cap with headroom
    return docs


def parse_fundamentals(doc: Document) -> dict[str, FundamentalsSnap]:
    payload = json.loads(doc.raw_bytes)
    out: dict[str, FundamentalsSnap] = {}
    for inst in payload.get("instruments") or []:
        fund = inst.get("fundamental") or {}
        symbol = str(inst.get("symbol") or fund.get("symbol") or "").upper()
        if not symbol:
            continue
        out[symbol] = FundamentalsSnap(
            symbol=symbol,
            pe=_dec(fund.get("peRatio")),
            pb=_dec(fund.get("pbRatio")),
            peg=_dec(fund.get("pegRatio")),
            div_yield=_dec(fund.get("dividendYield")),
            eps_ttm=_dec(fund.get("epsTTM")),
            market_cap=_dec(fund.get("marketCap")),
            beta=_dec(fund.get("beta")),
            # Schwab feeds ZEROS for the shortInt* fields (verified live
            # 2026-08-07 — schema stubs, not data). 0 → None: a true 0.0%
            # short interest doesn't exist; render absence, never fake zeros.
            # The same 0-is-absent rule applies to every panel field below:
            # exact 0.0 on a margin/ratio/dividend is the vendor's "no data"
            # (interestCoverage, divGrowthRate3Year, epsChangeYear are
            # all-zero stubs and deliberately not parsed at all).
            short_int_pct_float=_dec(fund.get("shortIntToFloat")) or None,
            short_int_days_to_cover=_dec(fund.get("shortIntDayToCover")) or None,
            high_52wk=_dec(fund.get("high52")),
            low_52wk=_dec(fund.get("low52")),
            ps=_dec(fund.get("prRatio")) or None,
            pcf=_dec(fund.get("pcfRatio")) or None,
            book_value_per_share=_dec(fund.get("bookValuePerShare")) or None,
            gross_margin_ttm=_dec(fund.get("grossMarginTTM")) or None,
            operating_margin_ttm=_dec(fund.get("operatingMarginTTM")) or None,
            net_margin_ttm=_dec(fund.get("netProfitMarginTTM")) or None,
            roe=_dec(fund.get("returnOnEquity")) or None,
            roa=_dec(fund.get("returnOnAssets")) or None,
            roi=_dec(fund.get("returnOnInvestment")) or None,
            current_ratio=_dec(fund.get("currentRatio")) or None,
            quick_ratio=_dec(fund.get("quickRatio")) or None,
            lt_debt_to_equity=_dec(fund.get("ltDebtToEquity")) or None,
            total_debt_to_equity=_dec(fund.get("totalDebtToEquity")) or None,
            rev_change_ttm=_dec(fund.get("revChangeTTM")) or None,
            rev_change_year=_dec(fund.get("revChangeYear")) or None,
            eps_change_ttm=_dec(fund.get("epsChangePercentTTM")) or None,
            div_amount=_dec(fund.get("dividendAmount")) or None,
            div_pay_amount=_dec(fund.get("dividendPayAmount")) or None,
            div_freq=int(fund["dividendFreq"]) if fund.get("dividendFreq") else None,
            next_div_ex_date=_vendor_date(fund.get("nextDividendDate")),
            next_div_pay_date=_vendor_date(fund.get("nextDividendPayDate")),
            shares_outstanding=_dec(fund.get("sharesOutstanding")) or None,
            avg_vol_3mo=_dec(fund.get("avg3MonthVolume")) or None,
        )
    return out


def _vendor_date(value) -> str | None:
    """'2026-08-14 00:00:00.0' → '2026-08-14'; anything malformed → None."""
    if not value:
        return None
    head = str(value)[:10]
    return head if re.fullmatch(r"\d{4}-\d{2}-\d{2}", head) else None


def latest_fundamentals(conn) -> tuple[dict[str, FundamentalsSnap], str | None]:
    """Merged snaps from the newest fundamentals batch (max scan_session)."""
    docs = repo.fetch(
        conn, Document,
        "source_name = 'schwab_fundamentals' AND query_params ? 'scan_session'")
    if not docs:
        return {}, None
    newest = max(d.query_params["scan_session"] for d in docs)
    merged: dict[str, FundamentalsSnap] = {}
    for d in docs:
        if d.query_params.get("scan_session") == newest:
            merged.update(parse_fundamentals(d))
    return merged, newest


def universe_fundamentals(conn, *, min_symbols: int = 300,
                          ) -> tuple[dict[str, FundamentalsSnap], str | None]:
    """Newest fundamentals batch with FULL-UNIVERSE coverage (the Saturday
    scan's ~503-name sweep), for cross-sectional percentiles. The Friday
    22:45 prefetch batch is theme-scoped and newer, so `latest_fundamentals`
    alone would shrink the peer set to a handful of names right when the
    deep dives render — this walks batches newest-first until one is broad
    enough. Pure derive-on-read."""
    docs = repo.fetch(
        conn, Document,
        "source_name = 'schwab_fundamentals' AND query_params ? 'scan_session'")
    by_session: dict[str, list[Document]] = {}
    for d in docs:
        by_session.setdefault(d.query_params["scan_session"], []).append(d)
    for session in sorted(by_session, reverse=True):
        merged: dict[str, FundamentalsSnap] = {}
        for d in by_session[session]:
            merged.update(parse_fundamentals(d))
        if len(merged) >= min_symbols:
            return merged, session
    return {}, None


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


# --------------------------------------------------------------------------- #
# Option chains (schwab_chains) — Phase D
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class OptionContract:
    put_call: str                # PUT | CALL
    strike: Decimal
    dte: int
    iv: Decimal | None           # percent points; vendor -999/0 → None
    open_interest: int
    volume: int


@dataclass(frozen=True)
class ChainSnap:
    symbol: str
    spot: Decimal | None
    contracts: tuple[OptionContract, ...]


def ingest_chain(conn, symbol: str, *, strike_count: int, dte_max: int,
                 session_tag: str) -> tuple[Document, bool]:
    today = datetime.now(timezone.utc).date()
    params = {
        "symbol": symbol, "contractType": "ALL",
        "strikeCount": strike_count, "strategy": "SINGLE",
        "fromDate": str(today), "toDate": str(today + timedelta(days=dte_max)),
    }
    return _fetch(conn, url=f"{BASE}/chains", params=params,
                  source_name="schwab_chains",
                  extra_stored={"scan_session": session_tag}, timeout=60.0)


def _chain_contracts(exp_map: dict, put_call: str):
    for _exp_key, strikes in (exp_map or {}).items():
        for _strike_key, contracts in (strikes or {}).items():
            for c in contracts or []:
                try:
                    iv = c.get("volatility")
                    iv = None if iv is None or float(iv) <= 0 or float(iv) > 500 else Decimal(str(iv))
                    yield OptionContract(
                        put_call=put_call,
                        strike=Decimal(str(c["strikePrice"])),
                        dte=int(c.get("daysToExpiration", -1)),
                        iv=iv,
                        open_interest=int(c.get("openInterest") or 0),
                        volume=int(c.get("totalVolume") or 0),
                    )
                except (KeyError, TypeError, ValueError, ArithmeticError):
                    continue


def parse_chain(doc: Document) -> ChainSnap:
    payload = json.loads(doc.raw_bytes)
    contracts = tuple(_chain_contracts(payload.get("putExpDateMap"), "PUT")) + \
        tuple(_chain_contracts(payload.get("callExpDateMap"), "CALL"))
    return ChainSnap(
        symbol=str(payload.get("symbol", "")).upper(),
        spot=_dec(payload.get("underlyingPrice")),
        contracts=contracts,
    )


def chain_docs_history(conn, symbol: str, *, limit: int = 90) -> list[Document]:
    """Trailing nightly chain documents for `symbol`, oldest first — the
    derive-on-read base for put/call percentiles."""
    docs = repo.fetch(
        conn, Document,
        "source_name = 'schwab_chains' AND query_params->>'symbol' = %s",
        (symbol,))
    return sorted(docs, key=lambda d: d.ingestion_time)[-limit:]


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
