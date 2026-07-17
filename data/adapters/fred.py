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


@dataclass(frozen=True)
class Observation:
    series_id: str
    observation_date: date
    value: float | None
    realtime_start: date


def ingest_series(conn, series_id: str) -> tuple[Document, bool]:
    api_key = os.environ["FRED_API_KEY"]
    today = datetime.now(timezone.utc).date().isoformat()
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
        for series in SLICE["fred_series"]:
            _, fetched = ingest_series(conn, series)
            results[series] = fetched
        conn.commit()
    return results


if __name__ == "__main__":
    for series, fetched in ingest_all().items():
        print(f"{series}: {'fetched' if fetched else 'cache hit'}")
