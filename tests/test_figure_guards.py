"""Guards added after slice run 1: extracted-figure ratios must be

unit-comparable and plausible; failures become classified data_incidents,
never silently-wrong COMPUTED metrics."""

from datetime import datetime, timezone
from decimal import Decimal

import psycopg
import pytest

from agents.roles.evidence import _units_comparable, compute_company_metrics
from agents.state import Claim
from data.db import dsn
from data.migrate import migrate

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def claim(figure_name: str, value: str, unit: str | None) -> Claim:
    return Claim(
        assertion_id=1, document_id=1, content_hash="h", symbol="GEV",
        quote="q", claim="c", figure_name=figure_name,
        figure_value=Decimal(value), figure_unit=unit,
    )


def test_units_comparable():
    assert _units_comparable(claim("a", "1", "USD_millions"), claim("b", "2", "usd_millions"))
    assert not _units_comparable(claim("a", "1", "USD_millions"), claim("b", "2", "USD_billions"))
    assert not _units_comparable(claim("a", "1", None), claim("b", "2", "USD_millions"))


def test_mismatched_units_raise_incident_not_metric(conn):
    """The run-1 bug reproduced: segment in millions vs total in billions must

    produce an INCONSISTENT incident and no revenue_share metric."""
    claims = [
        claim("segment_revenue_electrical", "2959", "USD_millions"),
        claim("total_revenue_fy", "38.1", "USD_billions"),
    ]
    with conn.transaction():
        metrics = compute_company_metrics(conn, "GEV", claims, [""], NOW)
        assert not any(m.name == "revenue_share" for m in metrics)
        n = conn.execute(
            "SELECT count(*) FROM data_incidents WHERE incident_class = 'INCONSISTENT'"
            " AND symbol = 'GEV'"
        ).fetchone()[0]
        assert n == 1
        raise psycopg.Rollback


def test_implausible_share_raises_incident(conn):
    claims = [
        claim("segment_revenue_electrical", "2959", "USD_millions"),
        claim("total_revenue_fy", "38", "USD_millions"),  # same unit but seg > total
    ]
    with conn.transaction():
        metrics = compute_company_metrics(conn, "GEV", claims, [""], NOW)
        assert not any(m.name == "revenue_share" for m in metrics)
        raise psycopg.Rollback


def test_matching_units_produce_metric(conn):
    claims = [
        claim("segment_revenue_electrical", "2959", "USD_millions"),
        claim("total_revenue_fy", "9000", "USD_millions"),
    ]
    with conn.transaction():
        metrics = compute_company_metrics(conn, "GEV", claims, [""], NOW)
        share = next(m for m in metrics if m.name == "revenue_share")
        assert Decimal("0.32") < share.value < Decimal("0.34")
        raise psycopg.Rollback