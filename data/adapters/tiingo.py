"""Tiingo EOD adapter. Honors contracts/tiingo.yaml (replaced Stooq 2026-07-17,

see docs/DECISIONS.md):
- raw + adjusted OHLCV cached immutably per symbol; API key never stored
- eligibility = next XNYS session open after ingestion
- derive-on-read parsing; missing sessions are absences, never filled

    uv run python -m data.adapters.tiingo
"""

import csv
import io
import json
import os
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from dotenv import load_dotenv

from config.loader import SLICE
from data.adapters.core import fetch_and_cache
from data.db import connect
from schemas.models import Document

load_dotenv()

LICENSE_NOTE = "Tiingo free tier: personal/internal use w/ attribution; no redistribution (contracts/tiingo.yaml)"
HISTORY_START = "2023-01-01"  # enough history for vol/momentum windows; config later if needed
SUPPORTED_TICKERS_URL = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
# Scout symbol rule (human decision 2026-07-29): US major exchanges only —
# no foreign listings, no OTC (foreign ADRs included). Tiingo tags NYSE ARCA
# and AMEX under these exchange codes.
US_EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "NYSE ARCA", "NYSE MKT", "BATS"}


@dataclass(frozen=True)
class Bar:
    symbol: str
    session: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    adj_open: Decimal
    adj_high: Decimal
    adj_low: Decimal
    adj_close: Decimal
    adj_volume: Decimal
    split_factor: Decimal
    div_cash: Decimal


def ingest_symbol(conn, symbol: str) -> tuple[Document, bool]:
    token = os.environ["TIINGO_API_KEY"]
    params = {"startDate": HISTORY_START, "format": "json", "token": token}
    doc, fetched = fetch_and_cache(
        conn,
        url=f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}/prices",
        params=params,
        stored_params={k: v for k, v in params.items() if k != "token"},  # key never stored
        source_name="tiingo",
        license_note=LICENSE_NOTE,
    )
    return doc, fetched


def parse_bars(doc: Document, symbol: str) -> list[Bar]:
    """Derive-on-read from the cached JSON; malformed rows are skipped

    (absence, not interpolation, per contract null rule)."""
    rows = json.loads(doc.raw_bytes)
    bars = []
    for row in rows:
        try:
            bars.append(
                Bar(
                    symbol=symbol,
                    session=date.fromisoformat(row["date"][:10]),
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=int(row.get("volume") or 0),
                    adj_open=Decimal(str(row.get("adjOpen", row["open"]))),
                    adj_high=Decimal(str(row.get("adjHigh", row["high"]))),
                    adj_low=Decimal(str(row.get("adjLow", row["low"]))),
                    adj_close=Decimal(str(row["adjClose"])),
                    adj_volume=Decimal(str(row.get("adjVolume", row.get("volume") or 0))),
                    split_factor=Decimal(str(row.get("splitFactor", 1))),
                    div_cash=Decimal(str(row.get("divCash", 0))),
                )
            )
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    return bars


def latest_bars(conn, symbol: str) -> list[Bar]:
    """Bars from the most recently ingested cached document for the symbol."""
    from data import repository as repo
    from schemas.models import Document as Doc

    docs = repo.fetch(
        conn, Doc,
        "source_name = 'tiingo' AND retrieval_url LIKE %s",
        (f"%/daily/{symbol.lower()}/prices",),
    )
    if not docs:
        return []
    newest = max(docs, key=lambda d: d.ingestion_time)
    return parse_bars(newest, symbol)


def refresh_symbol_directory(conn, max_age_days: int = 7) -> int:
    """Load Tiingo's supported-tickers list into symbol_directory (migration
    007). Static media download — no API key, not metered. Raw zip cached
    immutably (invariant 4); the table is derive-on-read reference data.
    Returns rows upserted (0 = still fresh). Fully automatic, no curation."""
    row = conn.execute("SELECT max(refreshed_at) FROM symbol_directory").fetchone()
    now = datetime.now(timezone.utc)
    if row and row[0] and row[0] > now - timedelta(days=max_age_days):
        return 0
    doc, _ = fetch_and_cache(
        conn,
        url=SUPPORTED_TICKERS_URL,
        params=None,
        source_name="tiingo_directory",
        license_note=LICENSE_NOTE,
        first_eligible_decision_time=now,  # reference data, never a decision input
        timeout=120.0,
    )
    with zipfile.ZipFile(io.BytesIO(doc.raw_bytes)) as zf:
        csv_bytes = zf.read(zf.namelist()[0])
    count = 0
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE _symdir_stage (LIKE symbol_directory INCLUDING ALL)"
            " ON COMMIT DROP"
        )
        with cur.copy(
            "COPY _symdir_stage (ticker, exchange, asset_type, price_currency,"
            " list_start, list_end, refreshed_at) FROM STDIN"
        ) as copy:
            seen: set[str] = set()
            for r in csv.DictReader(io.TextIOWrapper(io.BytesIO(csv_bytes))):
                ticker = (r.get("ticker") or "").strip().upper()
                if not ticker or ticker in seen:
                    continue
                seen.add(ticker)
                copy.write_row((
                    ticker,
                    (r.get("exchange") or "").strip().upper() or None,
                    (r.get("assetType") or "").strip() or None,
                    (r.get("priceCurrency") or "").strip() or None,
                    (r.get("startDate") or "").strip() or None,
                    (r.get("endDate") or "").strip() or None,
                    now,
                ))
                count += 1
        cur.execute(
            "INSERT INTO symbol_directory (ticker, exchange, asset_type,"
            " price_currency, list_start, list_end, refreshed_at)"
            " SELECT ticker, exchange, asset_type, price_currency, list_start,"
            "  list_end, refreshed_at FROM _symdir_stage"
            " ON CONFLICT (ticker) DO UPDATE SET exchange = EXCLUDED.exchange,"
            "  asset_type = EXCLUDED.asset_type,"
            "  price_currency = EXCLUDED.price_currency,"
            "  list_start = EXCLUDED.list_start, list_end = EXCLUDED.list_end,"
            "  refreshed_at = EXCLUDED.refreshed_at"
        )
    conn.commit()
    return count


def lookup_symbols(conn, symbols: list[str]) -> dict[str, dict | None]:
    """Directory rows for the given tickers; None marks an unknown symbol.
    Empty dict when the directory has never been populated (validation must
    then be skipped, never treated as all-unknown)."""
    if not conn.execute("SELECT 1 FROM symbol_directory LIMIT 1").fetchone():
        return {}
    out: dict[str, dict | None] = {}
    for symbol in symbols:
        row = conn.execute(
            "SELECT ticker, name, description, exchange, asset_type,"
            " price_currency, list_end FROM symbol_directory WHERE ticker = %s",
            (symbol.upper(),),
        ).fetchone()
        out[symbol.upper()] = None if row is None else {
            "ticker": row[0], "name": row[1], "description": row[2],
            "exchange": row[3], "asset_type": row[4],
            "price_currency": row[5], "list_end": row[6],
        }
    return out


def fill_symbol_meta(conn, symbols: list[str], max_age_days: int = 90) -> None:
    """Company name/description for the given tickers from Tiingo's metered
    meta endpoint, cached in symbol_directory (repeat lookups free). Best
    effort: any failure leaves the row without a name — the precheck and
    review degrade gracefully, never block."""
    token = os.environ.get("TIINGO_API_KEY")
    if not token:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    for symbol in symbols:
        symbol = symbol.upper()
        row = conn.execute(
            "SELECT meta_fetched_at FROM symbol_directory WHERE ticker = %s",
            (symbol,),
        ).fetchone()
        if row is None or (row[0] is not None and row[0] > cutoff):
            continue  # unknown ticker or meta still fresh
        try:
            params = {"format": "json", "token": token}
            doc, _ = fetch_and_cache(
                conn,
                url=f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}",
                params=params,
                stored_params={k: v for k, v in params.items() if k != "token"},
                source_name="tiingo_meta",
                license_note=LICENSE_NOTE,
                first_eligible_decision_time=datetime.now(timezone.utc),
                timeout=15.0,
            )
            meta = json.loads(doc.raw_bytes)
            conn.execute(
                "UPDATE symbol_directory SET name = %s, description = %s,"
                " meta_fetched_at = %s WHERE ticker = %s",
                (meta.get("name"), (meta.get("description") or "")[:1000] or None,
                 datetime.now(timezone.utc), symbol),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001 — enrichment is best-effort
            conn.rollback()
            print(f"tiingo meta fill failed for {symbol}: {e}", file=sys.stderr)


def ingest_all() -> dict[str, bool]:
    from config.loader import CONFIG

    controls = CONFIG.get("engine", {}).get("controls", {})
    benchmark_symbols = [s for s in (controls.get("spy_symbol"), controls.get("ewp_symbol")) if s]
    results = {}
    with connect() as conn:
        # Themes tables are authoritative post-unfreeze (all activations, so a
        # symbol activating next session has bars before its first gate check);
        # YAML fallback covers a fresh DB that predates migration 004's backfill.
        universe = [
            r[0] for r in conn.execute("SELECT DISTINCT symbol FROM theme_symbols")
        ]
        if not universe:
            universe = list(controls.get("momentum_universe", SLICE["symbols"]))
        # Per-symbol commit + containment: one bad ticker (404, transient 5xx)
        # must not discard every other symbol's documents for the night. A
        # missing bar for an ACTIVE universe symbol still halts loudly at the
        # completeness gate; SPY finality has its own guard.
        failures: list[str] = []
        for symbol in sorted(set(universe + benchmark_symbols)):
            try:
                _, fetched = ingest_symbol(conn, symbol)
                results[symbol] = fetched
                conn.commit()
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                failures.append(symbol)
                print(f"tiingo ingest FAILED for {symbol}: {e}", file=sys.stderr)
        if failures:
            from ops.alerts import notify
            notify(
                "Tiingo ingest failures",
                f"{', '.join(failures)} failed to fetch — completeness gate"
                " will halt if any is active in the universe",
                priority="high", tags="warning",
            )
    return results


if __name__ == "__main__":
    for sym, fetched in ingest_all().items():
        print(f"{sym}: {'fetched' if fetched else 'cache hit'}")
