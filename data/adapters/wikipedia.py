"""Wikipedia S&P 500 constituents adapter. Honors contracts/wikipedia_sp500.yaml
(momentum scan, DECISIONS.md 2026-07-29):
- weekly forward-only snapshot cached immutably; GICS sector + sub-industry
- sp500_constituents is derive-on-read reference data (like symbol_directory),
  never a decision input, never point-in-time membership history
- malformed rows are absences; a snapshot without the table fails loudly and
  the previous snapshot stays authoritative

    uv run python -m data.adapters.wikipedia
"""

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

from dotenv import load_dotenv

from data.adapters.core import fetch_and_cache
from data.db import connect
from schemas.models import Document

load_dotenv()

LICENSE_NOTE = ("Wikipedia CC BY-SA 4.0: cached locally, never republished; "
                "derived facts only (contracts/wikipedia_sp500.yaml)")
PAGE_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
# Sanity floor for a full replace: the index has ~503 constituents; a parse
# yielding far fewer means the page or parser broke — keep the old snapshot.
MIN_ROWS = 400


@dataclass(frozen=True)
class Constituent:
    ticker: str                      # Tiingo/desk style: BRK.B → BRK-B
    security: str | None
    gics_sector: str
    gics_sub_industry: str | None
    cik: str | None
    date_added: date | None


class _ConstituentsTable(HTMLParser):
    """Collects cell text rows from <table id="constituents"> only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._in_table = False
        self._table_depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self._in_table:
                self._table_depth += 1
            elif dict(attrs).get("id") == "constituents":
                self._in_table = True
                self._table_depth = 0
        if not self._in_table or self._table_depth:
            return
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if not self._in_table:
            return
        if tag == "table":
            if self._table_depth:
                self._table_depth -= 1
            else:
                self._in_table = False
            return
        if self._table_depth:
            return
        if tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            text = re.sub(r"\[\w{1,4}\]", "", text).strip()  # [1]-style footnotes
            if self._row is not None:
                self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._in_table and not self._table_depth and self._cell is not None:
            self._cell.append(data)


def parse_constituents(doc: Document) -> list[Constituent]:
    """Derive-on-read from the cached HTML; malformed rows are skipped
    (absence, not repair, per contract null rule)."""
    parser = _ConstituentsTable()
    parser.feed(doc.raw_bytes.decode("utf-8", errors="replace"))
    if not parser.rows:
        return []
    header = [h.lower() for h in parser.rows[0]]

    def col(*names: str) -> int | None:
        for i, h in enumerate(header):
            if any(n in h for n in names):
                return i
        return None

    i_sym = col("symbol", "ticker")
    i_sec = col("security", "company")
    i_sector = col("gics sector")
    i_sub = col("sub-industry")
    i_cik = col("cik")
    i_added = col("date added", "date first added")
    if i_sym is None or i_sector is None:
        return []

    out: list[Constituent] = []
    seen: set[str] = set()
    for row in parser.rows[1:]:
        try:
            ticker = row[i_sym].strip().upper().replace(".", "-")
            sector = row[i_sector].strip()
            if not re.fullmatch(r"[A-Z0-9-]{1,6}", ticker) or not sector:
                continue
            if ticker in seen:
                continue
            added = None
            if i_added is not None and i_added < len(row):
                m = re.search(r"\d{4}-\d{2}-\d{2}", row[i_added])
                if m:
                    added = date.fromisoformat(m.group(0))
            out.append(Constituent(
                ticker=ticker,
                security=(row[i_sec].strip() or None) if i_sec is not None and i_sec < len(row) else None,
                gics_sector=sector,
                gics_sub_industry=(row[i_sub].strip() or None) if i_sub is not None and i_sub < len(row) else None,
                cik=(row[i_cik].strip() or None) if i_cik is not None and i_cik < len(row) else None,
                date_added=added,
            ))
            seen.add(ticker)
        except (IndexError, ValueError):
            continue
    return out


def ingest_snapshot(conn) -> tuple[Document, bool]:
    now = datetime.now(timezone.utc)
    return fetch_and_cache(
        conn,
        url=PAGE_URL,
        params=None,
        source_name="wikipedia_sp500",
        license_note=LICENSE_NOTE,
        # Reference data, never a decision input (contract session_date_rule).
        first_eligible_decision_time=now,
        headers={"User-Agent": os.environ.get(
            "EDGAR_USER_AGENT", "Agents_TradingDesk research desk (personal)")},
        follow_redirects=True,
        timeout=60.0,
    )


def refresh_constituents(conn, max_age_days: int = 7) -> int:
    """Weekly refresh of sp500_constituents from a fresh snapshot. Full
    replace with snapshot_doc_hash provenance (derive-on-read, exactly one
    snapshot at a time). Returns rows written (0 = still fresh). A snapshot
    that parses below MIN_ROWS raises and leaves the old table untouched."""
    row = conn.execute("SELECT max(refreshed_at) FROM sp500_constituents").fetchone()
    now = datetime.now(timezone.utc)
    if row and row[0] and row[0] > now - timedelta(days=max_age_days):
        return 0
    doc, _ = ingest_snapshot(conn)
    constituents = parse_constituents(doc)
    if len(constituents) < MIN_ROWS:
        raise RuntimeError(
            f"wikipedia_sp500 snapshot parsed only {len(constituents)} rows"
            f" (< {MIN_ROWS}) — page or parser broke; keeping prior snapshot"
        )
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sp500_constituents")
        cur.executemany(
            "INSERT INTO sp500_constituents (ticker, security_name, gics_sector,"
            " gics_sub_industry, cik, date_added, snapshot_doc_hash, refreshed_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(c.ticker, c.security, c.gics_sector, c.gics_sub_industry,
              c.cik, c.date_added, doc.content_hash, now) for c in constituents],
        )
    conn.commit()
    return len(constituents)


def sector_map(conn) -> dict[str, tuple[str, str | None]]:
    """ticker → (gics_sector, gics_sub_industry) for every constituent.
    Empty dict when the table has never been populated."""
    return {
        r[0]: (r[1], r[2])
        for r in conn.execute(
            "SELECT ticker, gics_sector, gics_sub_industry FROM sp500_constituents"
        )
    }


def snapshot_refreshed_at(conn) -> datetime | None:
    row = conn.execute("SELECT max(refreshed_at) FROM sp500_constituents").fetchone()
    return row[0] if row else None


if __name__ == "__main__":
    with connect() as conn:
        written = refresh_constituents(conn)
    print(f"sp500_constituents: {written or 'fresh, skipped'}")
