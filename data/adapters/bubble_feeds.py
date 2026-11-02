"""AI-bubble exit monitor feeds (contracts/bubble_feeds.yaml). Operator-only,
quarantined from the analyst (invariant 5): never price_bars, never evidence.

Every response except Vast goes through fetch_and_cache (invariant 4). Vast's
license makes it display-only: one overwritten local file, never scored.
Spreadsheets are parsed with stdlib zip/xml; derive-on-read, a missing value
is an absence, never a guess.
"""

import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import httpx
from dotenv import load_dotenv

from data import repository as repo
from data.adapters.core import fetch_and_cache
from schemas.models import Document

load_dotenv()

XBRL_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json"
AZURE_URL = "https://prices.azure.com/api/retail/prices"
AZURE_SKU = "Standard_ND96isr_H100_v5"
AZURE_GPUS = 8
FINRA_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
BTOS_URL = "https://www.census.gov/hfp/btos/downloads/Sector.xlsx"
VAST_URL = "https://storage.googleapis.com/vast-public-gpu-pricing/gpu-pricing-public.json"
VAST_CACHE = Path(__file__).resolve().parents[2] / "reporting/output/bubble-monitor/vast-latest.json"
VAST_ATTRIBUTION = "Vast.ai marketplace (vast.ai/pricing); display only, not scored"
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) trading-desk/1.0"}

_NOW = lambda: datetime.now(timezone.utc)  # noqa: E731


def _latest(conn, source: str, where: str = "TRUE", params: tuple = ()) -> Document | None:
    docs = repo.fetch(conn, Document,
                      f"source_name = %s AND {where} ORDER BY ingestion_time DESC LIMIT 1",
                      (source, *params))
    return docs[0] if docs else None


# --------------------------------------------------------------------------- #
# SEC XBRL
# --------------------------------------------------------------------------- #

def ingest_xbrl(conn, symbol: str, cik: int, tag: str) -> tuple[Document, bool]:
    return fetch_and_cache(
        conn, url=XBRL_URL.format(cik=cik, tag=tag), params=None,
        stored_params={"symbol": symbol, "tag": tag},
        source_name="sec_xbrl", license_note="SEC EDGAR: public domain",
        headers={"User-Agent": os.environ["EDGAR_USER_AGENT"]},
        first_eligible_decision_time=_NOW(),
    )


def load_xbrl_facts(conn, symbol: str, tag: str) -> list[dict]:
    doc = _latest(conn, "sec_xbrl",
                  "query_params->>'symbol' = %s AND query_params->>'tag' = %s", (symbol, tag))
    if doc is None:
        return []
    return json.loads(doc.raw_bytes).get("units", {}).get("USD", [])


# --------------------------------------------------------------------------- #
# Azure retail prices (GPU list prices)
# --------------------------------------------------------------------------- #

def ingest_azure(conn) -> tuple[Document, bool]:
    return fetch_and_cache(
        conn, url=AZURE_URL,
        params={"$filter": f"armSkuName eq '{AZURE_SKU}' and serviceName eq 'Virtual Machines'"
                           " and armRegionName eq 'eastus'"},
        source_name="azure_retail", license_note="Azure Retail Prices API: public",
        first_eligible_decision_time=_NOW(),
    )


def parse_azure(doc: Document) -> dict[str, Decimal]:
    """{spot, reserved_1y} per GPU-hour. Linux only; Azure quotes a 1-year
    reservation as a total for the term."""
    out = {}
    for item in json.loads(doc.raw_bytes).get("Items", []):
        if "Windows" in item.get("productName", ""):
            continue
        price = Decimal(str(item["retailPrice"]))
        if item.get("type") == "Consumption" and item.get("skuName", "").endswith(" Spot"):
            out["spot"] = price / AZURE_GPUS
        elif item.get("type") == "Reservation" and item.get("reservationTerm") == "1 Year":
            out["reserved_1y"] = price / AZURE_GPUS / 8760
    return out


def azure_series(conn) -> dict[str, list[tuple[date, Decimal]]]:
    docs = repo.fetch(conn, Document, "source_name = 'azure_retail' ORDER BY ingestion_time")
    out: dict[str, list[tuple[date, Decimal]]] = {"azure_h100_spot": [], "azure_h100_res1y": []}
    for doc in docs:
        parsed = parse_azure(doc)
        day = doc.ingestion_time.date()
        if "spot" in parsed:
            out["azure_h100_spot"].append((day, parsed["spot"]))
        if "reserved_1y" in parsed:
            out["azure_h100_res1y"].append((day, parsed["reserved_1y"]))
    return out


# --------------------------------------------------------------------------- #
# Spreadsheets (stdlib)
# --------------------------------------------------------------------------- #

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _sheet_rows(raw: bytes, index: int) -> list[dict[str, str]]:
    """[{column letter: text}] for 1-based sheet `index`. Handles shared and
    inline strings; empty cells are simply absent."""
    with zipfile.ZipFile(BytesIO(raw)) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t"))
                      for si in root.findall("m:si", _NS)]
        sheet = ElementTree.fromstring(z.read(f"xl/worksheets/sheet{index}.xml"))
    rows = []
    for row in sheet.iter(f"{{{_NS['m']}}}row"):
        cells = {}
        for c in row.findall("m:c", _NS):
            col = re.match(r"[A-Z]+", c.get("r")).group()
            kind, v = c.get("t"), c.find("m:v", _NS)
            if kind == "inlineStr":
                cells[col] = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
            elif v is not None and v.text is not None:
                cells[col] = shared[int(v.text)] if kind == "s" else v.text
        rows.append(cells)
    return rows


def ingest_finra(conn) -> tuple[Document, bool]:
    return fetch_and_cache(conn, url=FINRA_URL, params=None, source_name="finra_margin",
                           license_note="FINRA margin statistics: public",
                           headers=BROWSER_UA, first_eligible_decision_time=_NOW())


def parse_finra(raw: bytes) -> list[tuple[date, Decimal]]:
    """Ascending debit balances ($M), dated at month start of the following
    month — FINRA publishes with a lag, so a month is never early."""
    out = []
    for row in _sheet_rows(raw, 1):
        m = re.fullmatch(r"(\d{4})-(\d{2})", row.get("A", ""))
        if not m or "B" not in row:
            continue
        year, month = int(m[1]), int(m[2])
        next_month = date(year + month // 12, month % 12 + 1, 1)
        out.append((next_month, Decimal(row["B"])))
    return sorted(out)


def ingest_btos(conn) -> tuple[Document, bool]:
    return fetch_and_cache(conn, url=BTOS_URL, params=None, source_name="census_btos",
                           license_note="US Census BTOS: public domain",
                           headers=BROWSER_UA, timeout=90.0,
                           first_eligible_decision_time=_NOW())


def _excel_date(serial: str) -> date:
    return date(1899, 12, 30) + timedelta(days=int(float(serial)))


def parse_btos(raw: bytes) -> list[tuple[date, Decimal]]:
    """Ascending economy-wide share of businesses using AI (%), dated by the
    survey cycle's last date; suppressed cells are absences."""
    rows = _sheet_rows(raw, 1)
    header = rows[0]
    cycle_end = {}
    for row in _sheet_rows(raw, 5)[1:]:
        values = list(row.values())
        for i, val in enumerate(values):
            if re.fullmatch(r"20\d{4}", val) and i + 2 < len(values):
                cycle_end[val] = _excel_date(values[i + 2])
                break
    target = next((r for r in rows[1:] if r.get("A") == "XX" and r.get("B") == "7"
                   and r.get("E") == "Yes"), None)
    if target is None:
        return []
    out = []
    for col, cycle in header.items():
        pct = target.get(col, "").rstrip("%")
        if cycle in cycle_end and pct:
            try:
                out.append((cycle_end[cycle], Decimal(pct)))
            except InvalidOperation:
                continue
    return sorted(out)


def latest_raw(conn, source: str) -> bytes | None:
    doc = _latest(conn, source)
    return doc.raw_bytes if doc else None


# --------------------------------------------------------------------------- #
# Vast (display only — see the contract's license note)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class VastPrice:
    name: str
    median: Decimal
    available: int
    median_30d_ago: Decimal | None


def refresh_vast() -> None:
    response = httpx.get(VAST_URL, timeout=30)
    response.raise_for_status()
    VAST_CACHE.parent.mkdir(parents=True, exist_ok=True)
    VAST_CACHE.write_bytes(response.content)


def read_vast(models: list[str]) -> tuple[list[VastPrice], str | None]:
    if not VAST_CACHE.exists():
        return [], None
    payload = json.loads(VAST_CACHE.read_text())
    out = []
    for name in models:
        g = payload.get("gpus", {}).get(name)
        if not g or not g.get("current"):
            continue
        daily = sorted(g.get("daily") or [], key=lambda d: d["date"])
        prior = daily[-30]["median"] if len(daily) >= 30 else None
        out.append(VastPrice(name, Decimal(str(g["current"]["median"])), g.get("available", 0),
                             None if prior is None else Decimal(str(prior))))
    return out, payload.get("updated")
