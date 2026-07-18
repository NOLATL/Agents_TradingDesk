"""Shared adapter machinery: immutable caching (invariant 4) and eligibility

derivation (invariant 2, per-contract session-date rules via exchange_calendars).
"""

import hashlib
from datetime import datetime, timezone
from typing import Any

import exchange_calendars as xcals
import httpx

from data import repository as repo
from schemas.models import Document

XNYS = xcals.get_calendar("XNYS")


def next_session_open(after: datetime) -> datetime:
    """First XNYS session open strictly after `after` (UTC in, UTC out).

    Never assumes Mon-Fri; holidays come from exchange_calendars.
    """
    minute = after.astimezone(timezone.utc)
    session = XNYS.minute_to_future_session(minute)
    return XNYS.session_open(session).to_pydatetime()


def content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _secret_param_values(
    params: dict[str, Any] | None,
    stored_params: dict[str, Any] | None,
) -> list[str]:
    """Values present in the request params but not preserved verbatim in the
    stored (scrubbed) copy — i.e. the secrets, per this module's contract.

    If a caller passes no stored_params, nothing is treated as secret (the
    caller has declared the whole request safe to persist).
    """
    if not params or stored_params is None:
        return []
    secrets = []
    for key, val in params.items():
        if stored_params.get(key) != val and val:
            secrets.append(str(val))
    return secrets


def _redact(text: str, secret_values: list[str]) -> str:
    for val in secret_values:
        if val:
            text = text.replace(val, "<redacted>")
    return text


def fetch_and_cache(
    conn,
    *,
    url: str,
    params: dict[str, Any] | None,
    source_name: str,
    license_note: str,
    first_eligible_decision_time: datetime | None = None,
    event_time: datetime | None = None,
    publication_time: datetime | None = None,
    headers: dict[str, str] | None = None,
    stored_params: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[Document, bool]:
    """GET url, cache the raw response immutably. Returns (document, was_fetched).

    Idempotency: if the response hash already exists in `documents`, the
    existing row is returned untouched and nothing is inserted (invariant 4:
    stored once, never mutated).

    If first_eligible_decision_time is None it defaults to the next session
    open after ingestion — the conservative rule for sources whose contract
    keys eligibility to ingestion (Stooq). Sources with publication-keyed
    rules (EDGAR, FRED) pass it explicitly.

    `stored_params` (defaults to `params`) is what gets persisted as the
    document's query_params — pass a scrubbed copy when the request params
    contain a secret (e.g. FRED api_key), which must never enter the DB.
    """
    response = httpx.get(url, params=params, headers=headers, timeout=timeout)
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # httpx puts the full request URL — api_key and all — into the exception
        # message, which then reaches logs and the systemd journal in plaintext.
        # The function's own contract already identifies the secrets: any param in
        # `params` that `stored_params` omits or masks is secret by definition.
        # Re-raise with those values redacted so the URL never leaks downstream.
        secret_values = _secret_param_values(params, stored_params)
        raise httpx.HTTPStatusError(
            _redact(str(exc), secret_values),
            request=exc.request,
            response=exc.response,
        ) from None
    raw = response.content
    h = content_hash(raw)

    existing = repo.document_by_hash(conn, h)
    if existing is not None:
        return existing, False

    ingestion_time = datetime.now(timezone.utc)
    doc = Document(
        content_hash=h,
        raw_bytes=raw,
        extracted_text=None,
        retrieval_url=url,
        query_params={
            k: str(v)
            for k, v in (stored_params if stored_params is not None else params or {}).items()
        },
        source_name=source_name,
        license_note=license_note,
        ingestion_time=ingestion_time,
        event_time=event_time,
        publication_time=publication_time,
        first_eligible_decision_time=(
            first_eligible_decision_time or next_session_open(ingestion_time)
        ),
    )
    repo.insert_document(conn, doc)
    return doc, True
