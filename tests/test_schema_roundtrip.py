"""Schema round-trip and invariant-structure tests (Stage 0).

No business logic — these prove the Pydantic models and the SQL DDL describe
the same schema, and that the load-bearing invariants are structurally
enforced where the schema can enforce them.

The DDL is applied to the scratch Compose Postgres (compose.scratch.yaml) and
information_schema is compared against schemas/models.py. Tests are skipped
with a clear message if the scratch database is not running:

    docker compose -f compose.scratch.yaml up -d --wait
    uv run pytest
    docker compose -f compose.scratch.yaml down -v
"""

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Union, get_args, get_origin

import psycopg
import pytest

from schemas import enums as desk_enums
from schemas.models import TABLE_MODELS

SCRATCH_DSN = "host=127.0.0.1 port=55432 dbname=trading_desk_scratch user=scratch password=scratch"

ENUM_SQL_NAMES = {
    desk_enums.ProvenanceType: "provenance_type",
    desk_enums.ExitType: "exit_type",
    desk_enums.ForecastOutcome: "forecast_outcome",
    desk_enums.ResolutionCause: "resolution_cause",
    desk_enums.ThesisStatus: "thesis_status",
    desk_enums.OrderStatus: "order_status",
    desk_enums.OrderSide: "order_side",
    desk_enums.MilestoneStatus: "milestone_status",
    desk_enums.AgentRole: "agent_role",
    desk_enums.IncidentClass: "incident_class",
    desk_enums.IncidentStatus: "incident_status",
}


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(SCRATCH_DSN, connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip(
            "scratch Postgres not running — start it with: "
            "docker compose -f compose.scratch.yaml up -d --wait"
        )
    with open("schemas/ddl.sql") as f:
        ddl = f.read()
    connection.execute(ddl)
    # Idempotency requirement: applying the DDL twice must be safe.
    connection.execute(ddl)
    connection.commit()
    yield connection
    connection.close()


def sql_columns(conn, table: str) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT column_name, is_nullable, data_type, udt_name, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    ).fetchall()
    return {
        r[0]: {"nullable": r[1] == "YES", "data_type": r[2], "udt": r[3], "default": r[4]}
        for r in rows
    }


def is_optional(annotation) -> bool:
    return get_origin(annotation) is Union and type(None) in get_args(annotation)


def unwrap(annotation):
    if is_optional(annotation):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return args[0]
    return annotation


# --------------------------------------------------------------------------- #
# Round-trip: model ↔ table parity
# --------------------------------------------------------------------------- #

def test_all_spec_tables_present(conn):
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        )
    }
    missing = set(TABLE_MODELS) - tables
    assert not missing, f"tables in models but not in DDL: {missing}"


@pytest.mark.parametrize("table,model", TABLE_MODELS.items(), ids=TABLE_MODELS.keys())
def test_column_field_parity(conn, table, model):
    cols = sql_columns(conn, table)
    fields = set(model.model_fields)
    assert fields == set(cols), (
        f"{table}: model-only={fields - set(cols)}, sql-only={set(cols) - fields}"
    )


@pytest.mark.parametrize("table,model", TABLE_MODELS.items(), ids=TABLE_MODELS.keys())
def test_nullability_parity(conn, table, model):
    """A model field is optional iff its SQL column is nullable.

    Exception: `id` is Optional in Python (unset before insert) but the SQL
    identity PK is NOT NULL. Fields with a Python default map to
    NOT NULL DEFAULT in SQL and are asserted non-nullable.
    """
    cols = sql_columns(conn, table)
    for name, field in model.model_fields.items():
        if name == "id":
            assert not cols[name]["nullable"]
            continue
        model_optional = is_optional(field.annotation) and field.default is None
        assert cols[name]["nullable"] == model_optional, (
            f"{table}.{name}: SQL nullable={cols[name]['nullable']}, "
            f"model optional={model_optional}"
        )


@pytest.mark.parametrize("table,model", TABLE_MODELS.items(), ids=TABLE_MODELS.keys())
def test_type_parity(conn, table, model):
    """Spot-check that Python types map to compatible SQL types."""
    compatible = {
        str: {"text"},
        int: {"bigint", "integer"},
        float: {"double precision"},
        bool: {"boolean"},
        bytes: {"bytea"},
        Decimal: {"numeric"},
        datetime: {"timestamp with time zone"},
        date: {"date"},
        dict: {"jsonb"},
    }
    cols = sql_columns(conn, table)
    for name, field in model.model_fields.items():
        py = unwrap(field.annotation)
        origin = get_origin(py)
        if origin is dict:
            py = dict
        if isinstance(py, type) and issubclass(py, enum.Enum):
            assert cols[name]["udt"] == ENUM_SQL_NAMES[py], (
                f"{table}.{name}: expected SQL enum {ENUM_SQL_NAMES[py]}, got {cols[name]['udt']}"
            )
            continue
        expected = compatible.get(py)
        if expected is None:
            pytest.fail(f"{table}.{name}: unmapped Python type {py!r}")
        assert cols[name]["data_type"] in expected, (
            f"{table}.{name}: Python {py.__name__} vs SQL {cols[name]['data_type']}"
        )


# --------------------------------------------------------------------------- #
# Enum integrity
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("py_enum,sql_name", ENUM_SQL_NAMES.items(), ids=ENUM_SQL_NAMES.values())
def test_enum_values_match(conn, py_enum, sql_name):
    rows = conn.execute(
        """
        SELECT e.enumlabel FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = %s ORDER BY e.enumsortorder
        """,
        (sql_name,),
    ).fetchall()
    assert [r[0] for r in rows] == [m.value for m in py_enum]


def test_resolution_cause_excludes_trade_exits():
    """Invariant 7 vocabulary check: a trade exit is not a resolution cause."""
    causes = {m.value for m in desk_enums.ResolutionCause}
    assert "PRICE_STOP" not in causes
    assert "VALUATION_EXIT" not in causes


# --------------------------------------------------------------------------- #
# Invariant structure enforced in SQL
# --------------------------------------------------------------------------- #

def not_null(conn, table, column):
    return not sql_columns(conn, table)[column]["nullable"]


def test_provenance_type_not_nullable(conn):
    assert not_null(conn, "assertions", "type")


def test_eligibility_columns_not_nullable(conn):
    assert not_null(conn, "documents", "ingestion_time")
    assert not_null(conn, "documents", "first_eligible_decision_time")


def test_nav_taint_flag_present_and_not_nullable(conn):
    assert not_null(conn, "daily_nav", "contains_forced_resolution")
    assert not_null(conn, "benchmarks_nav", "contains_forced_resolution")


def test_documents_immutability_flag(conn):
    """is_raw_immutable is NOT NULL, defaults TRUE, and CHECK-rejects FALSE."""
    assert not_null(conn, "documents", "is_raw_immutable")
    with conn.transaction():
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO documents (content_hash, raw_bytes, retrieval_url, source_name,"
                    " is_raw_immutable, ingestion_time, first_eligible_decision_time)"
                    " VALUES ('x', ''::bytea, 'u', 's', FALSE, now(), now())"
                )


def test_thesis_requires_all_four_exit_types(conn):
    """§5: exit_criteria must contain all four exit-type keys."""
    with conn.transaction():
        conn.execute(
            "INSERT INTO experiments (name, registry, created_at)"
            " VALUES ('t-exit-check', '{}'::jsonb, now())"
        )
        exp_id = conn.execute(
            "SELECT id FROM experiments WHERE name='t-exit-check'"
        ).fetchone()[0]
        incomplete = '{"PRICE_STOP": "x", "CAUSAL_BREAK": "x", "VALUATION_EXIT": "x"}'
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO theses (experiment_id, title, strategy_tag, status, causal_chain,"
                    " exposure_basket_approach, valuation_check, exit_criteria, model_tag,"
                    " prompt_version, created_at)"
                    " VALUES (%s, 't', 'tag', 'DRAFT', 'c', 'e', 'v', %s::jsonb, 'm', 'p', now())",
                    (exp_id, incomplete),
                )
        raise psycopg.Rollback  # leave no rows behind


def test_forecast_probability_bounds(conn):
    with conn.transaction():
        conn.execute(
            "INSERT INTO experiments (name, registry, created_at)"
            " VALUES ('t-prob-check', '{}'::jsonb, now())"
        )
        exp_id = conn.execute(
            "SELECT id FROM experiments WHERE name='t-prob-check'"
        ).fetchone()[0]
        full = ('{"PRICE_STOP": "x", "CAUSAL_BREAK": "x",'
                ' "VALUATION_EXIT": "x", "TIME_STOP": "x"}')
        conn.execute(
            "INSERT INTO theses (experiment_id, title, strategy_tag, status, causal_chain,"
            " exposure_basket_approach, valuation_check, exit_criteria, model_tag,"
            " prompt_version, created_at)"
            " VALUES (%s, 't', 'tag', 'DRAFT', 'c', 'e', 'v', %s::jsonb, 'm', 'p', now())",
            (exp_id, full),
        )
        thesis_id = conn.execute("SELECT max(id) FROM theses").fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO forecasts (thesis_id, statement, probability, horizon_end,"
                    " resolution_criterion, created_by, model_tag, prompt_version, created_at)"
                    " VALUES (%s, 's', 1.5, '2027-01-01', 'r', 'RESEARCH_ANALYST', 'm', 'p', now())",
                    (thesis_id,),
                )
        raise psycopg.Rollback


def test_trades_are_append_only(conn):
    """The ledger is immutable: UPDATE and DELETE on trades must fail."""
    with conn.transaction():
        conn.execute(
            "INSERT INTO experiments (name, registry, created_at)"
            " VALUES ('t-immutable', '{}'::jsonb, now())"
        )
        exp_id = conn.execute("SELECT id FROM experiments WHERE name='t-immutable'").fetchone()[0]
        full = ('{"PRICE_STOP": "x", "CAUSAL_BREAK": "x",'
                ' "VALUATION_EXIT": "x", "TIME_STOP": "x"}')
        conn.execute(
            "INSERT INTO theses (experiment_id, title, strategy_tag, status, causal_chain,"
            " exposure_basket_approach, valuation_check, exit_criteria, model_tag,"
            " prompt_version, created_at)"
            " VALUES (%s, 't', 'tag', 'DRAFT', 'c', 'e', 'v', %s::jsonb, 'm', 'p', now())",
            (exp_id, full),
        )
        thesis_id = conn.execute("SELECT max(id) FROM theses").fetchone()[0]
        conn.execute(
            "INSERT INTO orders (experiment_id, thesis_id, symbol, side, status, strategy_tag,"
            " proposed_qty, proposed_at, created_at)"
            " VALUES (%s, %s, 'TEST', 'BUY', 'FILLED', 'tag', 1, now(), now())",
            (exp_id, thesis_id),
        )
        order_id = conn.execute("SELECT max(id) FROM orders").fetchone()[0]
        conn.execute(
            "INSERT INTO trades (order_id, symbol, side, qty, fill_price, slippage_applied,"
            " strategy_tag, executed_at, created_at)"
            " VALUES (%s, 'TEST', 'BUY', 1, 100, 0.05, 'tag', now(), now())",
            (order_id,),
        )
        trade_id = conn.execute("SELECT max(id) FROM trades").fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE trades SET qty = 2 WHERE id = %s", (trade_id,))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("DELETE FROM trades WHERE id = %s", (trade_id,))
        raise psycopg.Rollback


def test_frozen_position_requires_mark(conn):
    """§6 freeze-and-mark: a frozen position without a mark is rejected."""
    with conn.transaction():
        conn.execute(
            "INSERT INTO experiments (name, registry, created_at)"
            " VALUES ('t-freeze', '{}'::jsonb, now())"
        )
        exp_id = conn.execute("SELECT id FROM experiments WHERE name='t-freeze'").fetchone()[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute(
                    "INSERT INTO positions (experiment_id, symbol, qty, avg_cost, frozen, as_of)"
                    " VALUES (%s, 'TEST', 1, 100, TRUE, now())",
                    (exp_id,),
                )
        raise psycopg.Rollback


# --------------------------------------------------------------------------- #
# Pydantic-side round trip
# --------------------------------------------------------------------------- #

def test_pydantic_json_round_trip():
    """Each model survives model_dump_json → model_validate_json unchanged."""
    from schemas.models import Forecast

    f = Forecast(
        thesis_id=1,
        statement="Aggregate hyperscaler capex guidance stays above $X",
        probability=0.7,
        horizon_end=date(2027, 6, 30),
        resolution_criterion="Sum of guided capex in 10-Qs of MSFT/GOOGL/AMZN/META > X",
        created_by=desk_enums.AgentRole.RESEARCH_ANALYST,
        model_tag="qwen3:30b-a3b",
        prompt_version="v0",
        created_at=datetime(2026, 7, 17, 12, 0, 0),
    )
    assert Forecast.model_validate_json(f.model_dump_json()) == f


def test_extra_fields_forbidden():
    """extra='forbid' everywhere: a stray field is a schema error, not silence."""
    from pydantic import ValidationError

    from schemas.models import DailyNav

    with pytest.raises(ValidationError):
        DailyNav(
            experiment_id=1,
            nav_date=date(2026, 7, 17),
            nav=Decimal("10000"),
            cash=Decimal("10000"),
            positions_value=Decimal("0"),
            surprise_column=1,
        )
