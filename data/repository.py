"""Thin repository over the Stage 0 schema. psycopg only — no ORM.

One generic, model-driven insert/fetch path so the Pydantic models in
schemas/models.py stay the single source of truth. Insert paths enforce the
structural invariants before SQL ever runs:
- assertions must carry a ProvenanceType (invariant 1)
- documents must carry ingestion + first_eligible_decision_time (invariant 2)
- reads of source documents go through `eligible_documents(clock)` — records
  whose eligibility is ahead of the simulation clock are not returned.
"""

import enum
from datetime import datetime
from typing import Any, TypeVar

from psycopg.types.json import Json

from schemas.enums import SQL_ENUM_NAMES
from schemas.models import (
    Assertion,
    DeskModel,
    Document,
    TABLE_MODELS,
)

M = TypeVar("M", bound=DeskModel)

MODEL_TABLES = {model: table for table, model in TABLE_MODELS.items()}


def _adapt(value: Any) -> Any:
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return Json(value)
    return value


def _placeholder(model: type[DeskModel], field: str) -> str:
    """%s, with an explicit cast for SQL enum columns (psycopg sends text)."""
    annotation = model.model_fields[field].annotation
    for py_enum, sql_name in SQL_ENUM_NAMES.items():
        if py_enum is annotation or (
            hasattr(annotation, "__args__") and py_enum in getattr(annotation, "__args__", ())
        ):
            return f"%s::{sql_name}"
    return "%s"


def insert(conn, obj: DeskModel) -> int:
    """Generic insert; returns the new row id and sets it on the object."""
    table = MODEL_TABLES[type(obj)]
    data = {k: v for k, v in obj.model_dump().items() if k != "id"}
    cols = ", ".join(data)
    placeholders = ", ".join(_placeholder(type(obj), k) for k in data)
    row = conn.execute(
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) RETURNING id",
        tuple(_adapt(v) for v in data.values()),
    ).fetchone()
    obj.id = row[0]
    return obj.id


def fetch(conn, model: type[M], where: str = "TRUE", params: tuple = ()) -> list[M]:
    table = MODEL_TABLES[model]
    cols = list(model.model_fields)
    rows = conn.execute(
        f"SELECT {', '.join(cols)} FROM {table} WHERE {where}", params
    ).fetchall()
    return [model.model_validate(dict(zip(cols, r))) for r in rows]


def fetch_one(conn, model: type[M], where: str, params: tuple = ()) -> M | None:
    results = fetch(conn, model, where, params)
    return results[0] if results else None


# --------------------------------------------------------------------------- #
# Invariant-enforcing paths
# --------------------------------------------------------------------------- #

class InvariantViolation(Exception):
    pass


def insert_assertion(conn, assertion: Assertion) -> int:
    if assertion.type is None:  # unreachable via Pydantic, kept as a hard stop
        raise InvariantViolation("assertion without provenance type (invariant 1)")
    return insert(conn, assertion)


def document_by_hash(conn, content_hash: str) -> Document | None:
    return fetch_one(conn, Document, "content_hash = %s", (content_hash,))


def insert_document(conn, doc: Document) -> int:
    if doc.ingestion_time is None or doc.first_eligible_decision_time is None:
        raise InvariantViolation("document without eligibility timestamps (invariant 2)")
    if not doc.is_raw_immutable:
        raise InvariantViolation("documents are immutable raw (invariant 4)")
    return insert(conn, doc)


def eligible_documents(
    conn, clock: datetime, where: str = "TRUE", params: tuple = ()
) -> list[Document]:
    """The ONLY sanctioned read path for source documents (invariant 2).

    Never returns a record whose first_eligible_decision_time is after `clock`.
    """
    return fetch(
        conn,
        Document,
        f"first_eligible_decision_time <= %s AND ({where})",
        (clock, *params),
    )
