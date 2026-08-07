"""USAspending.gov adapter (2026-08-07 scale-up P4). Honors
contracts/usaspending.yaml: one POST per context build sweeping the largest
recent federal contract awards — forced-spending catalyst evidence for the
scout's [Un] section. Raw responses cached immutably (invariant 4) with the
POST body persisted in query_params._post_body; parsing is a deterministic
read of the cached bytes. Config lives in config/discovery.yaml `usaspending`.

    uv run python -m data.adapters.usaspending
"""

import json
from datetime import date, datetime, timedelta, timezone

from data.adapters.core import fetch_and_cache
from data.db import connect
from schemas.models import Document

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
USER_AGENT = "AgentsTradingDesk research jaredcarollo+ai@proton.me"
LICENSE_NOTE = "USAspending.gov public federal spending data"
FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency",
          "Awarding Sub Agency", "Start Date", "Description", "NAICS"]
# A/B/C/D = definitive contracts (BPA/IDV excluded — the scout wants firm awards)
AWARD_TYPE_CODES = ["A", "B", "C", "D"]


def _config() -> dict:
    from data.adapters import rss
    return dict(rss.discovery_config().get("usaspending", {}) or {})


def ingest_awards(conn, *, as_of: date | None = None) -> tuple[Document, bool]:
    cfg = _config()
    days = int(cfg.get("days", 30))
    end = as_of or datetime.now(timezone.utc).date()
    body = {
        "filters": {
            "time_period": [{
                "start_date": (end - timedelta(days=days)).isoformat(),
                "end_date": end.isoformat(),
            }],
            "award_type_codes": AWARD_TYPE_CODES,
            "award_amounts": [
                {"lower_bound": int(cfg.get("min_amount_usd", 50_000_000))}
            ],
        },
        "fields": FIELDS,
        "sort": "Award Amount",
        "order": "desc",
        "limit": int(cfg.get("limit", 12)),
        "page": 1,
    }
    return fetch_and_cache(
        conn,
        url=API_URL,
        params=None,
        method="POST",
        json_body=body,
        source_name="usaspending",
        license_note=LICENSE_NOTE,
        first_eligible_decision_time=datetime.now(timezone.utc),
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )


def parse_awards(doc: Document) -> list[dict]:
    """Deterministic parse of a cached response -> award dicts, largest
    first (API sort preserved). Rows without an amount are dropped."""
    try:
        payload = json.loads(doc.raw_bytes)
    except ValueError:
        return []
    out = []
    for row in payload.get("results", []) or []:
        amount = row.get("Award Amount")
        if amount in (None, ""):
            continue
        out.append({
            "recipient": row.get("Recipient Name"),
            "amount": float(amount),
            "agency": row.get("Awarding Agency"),
            "sub_agency": row.get("Awarding Sub Agency"),
            "start_date": row.get("Start Date"),
            "description": (row.get("Description") or "").strip()[:140],
            "naics": row.get("NAICS"),
        })
    return out


def recent_awards(conn) -> list[dict]:
    """Fetch-or-cache today's sweep and parse it. Raises on network/HTTP
    failure — the caller treats the section as enrichment."""
    doc, _ = ingest_awards(conn)
    return parse_awards(doc)


if __name__ == "__main__":
    with connect() as conn:
        for award in recent_awards(conn):
            print(f"${award['amount'] / 1e6:,.0f}M  {award['agency']}"
                  f" -> {award['recipient']}  ({award['start_date']})")
        conn.commit()
