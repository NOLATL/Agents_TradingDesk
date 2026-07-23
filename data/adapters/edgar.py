"""SEC EDGAR adapter. Honors contracts/edgar.yaml:

- declared User-Agent with contact email; throttled well under 10 req/s
- filings cached immutably; amendments are new documents, never mutations
- eligibility: acceptance datetime (ET) → 17:30 dissemination cutoff rule →
  next XNYS session open (exchange_calendars; never Mon-Fri assumptions)

    uv run python -m data.adapters.edgar
"""

import json
import os
import sys
import time as time_mod
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from config.loader import SLICE
from data.adapters.core import fetch_and_cache, next_session_open
from data.db import connect
from schemas.models import Document

load_dotenv()

ET = ZoneInfo("America/New_York")
LICENSE_NOTE = "SEC EDGAR: US public domain; fair-access UA + rate rules (contracts/edgar.yaml)"
DISSEMINATION_CUTOFF = time(17, 30)
REQUEST_DELAY_S = 0.15  # ~6.7 req/s, under the 10/s fair-access ceiling


def _headers() -> dict[str, str]:
    return {"User-Agent": os.environ["EDGAR_USER_AGENT"]}


def filing_eligibility(acceptance_et: datetime) -> datetime:
    """Contract session-date rule: accepted after 17:30 ET counts as published

    the next business day; then map to the following session open."""
    effective = acceptance_et
    if acceptance_et.timetz().replace(tzinfo=None) >= DISSEMINATION_CUTOFF:
        effective = acceptance_et + timedelta(days=1)
    return next_session_open(effective)


def resolve_ciks(conn, symbols: list[str]) -> dict[str, str]:
    """Ticker → zero-padded CIK via SEC's mapping file (cached immutably too)."""
    doc, _ = fetch_and_cache(
        conn,
        url="https://www.sec.gov/files/company_tickers.json",
        params=None,
        source_name="sec_edgar",
        license_note=LICENSE_NOTE,
        headers=_headers(),
    )
    mapping = json.loads(doc.raw_bytes)
    by_ticker = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in mapping.values()}
    unmapped = [s for s in symbols if s not in by_ticker]
    if unmapped:
        print(f"edgar: no CIK for {', '.join(unmapped)} — skipped", file=sys.stderr)
    return {s: by_ticker[s] for s in symbols if s in by_ticker}


def ingest_company(conn, symbol: str, cik: str) -> list[tuple[Document, bool]]:
    """Fetch the submissions index, then the N most recent primary documents

    of the configured forms."""
    time_mod.sleep(REQUEST_DELAY_S)
    index_doc, _ = fetch_and_cache(
        conn,
        url=f"https://data.sec.gov/submissions/CIK{cik}.json",
        params=None,
        source_name="sec_edgar",
        license_note=LICENSE_NOTE,
        headers=_headers(),
    )
    recent = json.loads(index_doc.raw_bytes)["filings"]["recent"]
    wanted_forms = set(SLICE["edgar_forms"])
    limit = SLICE["edgar_filings_per_company"]

    results = []
    count = 0
    for i, form in enumerate(recent["form"]):
        if form not in wanted_forms or count >= limit:
            continue
        accession = recent["accessionNumber"][i].replace("-", "")
        primary = recent["primaryDocument"][i]
        acceptance = datetime.fromisoformat(recent["acceptanceDateTime"][i]).astimezone(ET)
        report_date = recent["reportDate"][i] or recent["filingDate"][i]
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{primary}"
        time_mod.sleep(REQUEST_DELAY_S)
        doc, fetched = fetch_and_cache(
            conn,
            url=url,
            params=None,
            stored_params={"symbol": symbol, "form": form, "accession": accession},
            source_name="sec_edgar",
            license_note=LICENSE_NOTE,
            event_time=datetime.fromisoformat(report_date + "T00:00:00+00:00"),
            publication_time=acceptance,
            first_eligible_decision_time=filing_eligibility(acceptance),
            headers=_headers(),
        )
        results.append((doc, fetched))
        count += 1
    return results


def active_theme_symbols(conn) -> list[str]:
    """Filings are ingested for accepted themes' symbols — theme_symbols is
    the authoritative post-unfreeze universe, same source as the Tiingo
    ingest (the YAML theme list is genesis documentation only). Falls back
    to the Stage 1 slice universe on a fresh DB that predates migration 004."""
    symbols = [
        r[0] for r in conn.execute("SELECT DISTINCT symbol FROM theme_symbols")
    ]
    return sorted(symbols) if symbols else sorted(set(SLICE["symbols"]))


def ingest_all() -> dict[str, int]:
    counts = {}
    with connect() as conn:
        ciks = resolve_ciks(conn, active_theme_symbols(conn))
        conn.commit()
        # Per-company commit + containment (mirrors tiingo.ingest_all): one
        # company's fetch failure must not discard the rest of the night.
        failures: list[str] = []
        for symbol, cik in ciks.items():
            try:
                docs = ingest_company(conn, symbol, cik)
                counts[symbol] = sum(1 for _, fetched in docs if fetched)
                conn.commit()
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                failures.append(symbol)
                print(f"edgar ingest FAILED for {symbol}: {e}", file=sys.stderr)
        if failures:
            from ops.alerts import notify
            notify(
                "EDGAR ingest failures",
                f"{', '.join(failures)} failed — filings evidence may be stale"
                " for these names",
                priority="high", tags="warning",
            )
    return counts


if __name__ == "__main__":
    for sym, new in ingest_all().items():
        print(f"{sym}: {new} new filings cached")
