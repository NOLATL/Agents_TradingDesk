"""FRED/ALFRED adapter. Honors contracts/fred.yaml:

- ALFRED realtime bounds so each vintage is a distinct immutable document
  (current-series retrieval is look-ahead for revision-prone series)
- eligibility = vintage availability (realtime_start of the fetch window)
  mapped to the next XNYS session open
- '.' observations stay null — parsing keeps them as None, never interpolates

    uv run python -m data.adapters.fred
"""

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from config.loader import SLICE
from data.adapters.core import fetch_and_cache, next_session_open
from data.db import connect
from schemas.models import Document

load_dotenv()

LICENSE_NOTE = (
    "FRED API: non-commercial/internal use with attribution; per-series "
    "third-party restrictions recorded (contracts/fred.yaml)"
)


# FRED is published by the St. Louis Fed and validates realtime bounds against
# *its own* calendar date, not UTC: "realtime_start can not be after today's
# date". Deriving the vintage date from UTC therefore breaks every run that
# happens after 19:00 US Central, because UTC has already rolled over to
# tomorrow while FRED is still on today.
#
# The daily monitor fires at 23:00 America/New_York (22:00 Central, 03:00 UTC
# next day), so it requested a vintage that did not exist yet and FRED returned
# 400 — deterministically, every weeknight, in both EDT and EST. Observed
# 2026-07-17 23:01 on series IPUTIL and reproduced against the live API.
#
# Anchoring to FRED's own timezone is correct in every window, including the
# 00:00-01:00 Eastern hour where Eastern is a day ahead of Central and the
# codebase's usual XNYS/Eastern convention would still fail.
FRED_TZ = ZoneInfo("America/Chicago")


def _vintage_date() -> date:
    """Today, as FRED reckons it. See FRED_TZ."""
    return datetime.now(FRED_TZ).date()


@dataclass(frozen=True)
class Observation:
    series_id: str
    observation_date: date
    value: float | None
    realtime_start: date


def ingest_series(conn, series_id: str) -> tuple[Document, bool]:
    api_key = os.environ["FRED_API_KEY"]
    today = _vintage_date().isoformat()
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        # ALFRED: pin the vintage window to today so the stored document is
        # exactly "what was knowable now"; historical vintages arrive as
        # separate fetches with their own windows when needed.
        "realtime_start": today,
        "realtime_end": today,
    }
    ingestion = datetime.now(timezone.utc)
    doc, fetched = fetch_and_cache(
        conn,
        url="https://api.stlouisfed.org/fred/series/observations",
        params=params,
        stored_params={k: v for k, v in params.items() if k != "api_key"},  # key never stored
        source_name="fred",
        license_note=LICENSE_NOTE,
        publication_time=ingestion,
        first_eligible_decision_time=next_session_open(ingestion),
    )
    return doc, fetched


def parse_observations(doc: Document, series_id: str) -> list[Observation]:
    payload = json.loads(doc.raw_bytes)
    out = []
    for obs in payload.get("observations", []):
        raw_value = obs.get("value", ".")
        out.append(
            Observation(
                series_id=series_id,
                observation_date=date.fromisoformat(obs["date"]),
                value=None if raw_value in (".", "") else float(raw_value),
                realtime_start=date.fromisoformat(obs["realtime_start"]),
            )
        )
    return out


def ingest_all() -> dict[str, bool]:
    results = {}
    with connect() as conn:
        # Accepted themes may carry their own macro series (research_config);
        # macro_points silently skips anything never ingested, so the nightly
        # pull must cover them alongside the slice defaults.
        theme_series = [
            s
            for (rc,) in conn.execute(
                "SELECT research_config FROM themes WHERE status = 'ACCEPTED'"
                " AND research_config ? 'fred_series'"
            )
            for s in rc["fred_series"]
        ]
        for series in sorted(set(SLICE["fred_series"]) | set(theme_series)):
            _, fetched = ingest_series(conn, series)
            results[series] = fetched
        conn.commit()
    return results


if __name__ == "__main__":
    for series, fetched in ingest_all().items():
        print(f"{series}: {'fetched' if fetched else 'cache hit'}")
