"""Tiingo EOD adapter. Honors contracts/tiingo.yaml (replaced Stooq 2026-07-17,

see docs/DECISIONS.md):
- raw + adjusted OHLCV cached immutably per symbol; API key never stored
- eligibility = next XNYS session open after ingestion
- derive-on-read parsing; missing sessions are absences, never filled

    uv run python -m data.adapters.tiingo
"""

import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from dotenv import load_dotenv

from config.loader import SLICE
from data.adapters.core import fetch_and_cache
from data.db import connect
from schemas.models import Document

load_dotenv()

LICENSE_NOTE = "Tiingo free tier: personal/internal use w/ attribution; no redistribution (contracts/tiingo.yaml)"
HISTORY_START = "2023-01-01"  # enough history for vol/momentum windows; config later if needed


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
