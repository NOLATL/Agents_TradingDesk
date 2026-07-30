"""Native Ollama /api/chat transport (2026-07-29): num_ctx is honored only
on the native endpoint (the OpenAI-compat /v1 shim silently drops options),
so structured_call must send stream=false, the JSON schema as `format`, and
temperature/num_ctx inside options. httpx is mocked; DB is real (AgentRun
rows are logged per call — structured_call commits, so tests clean up their
own rows instead of using a rollback transaction).
"""

import psycopg
import pytest
from pydantic import BaseModel, ConfigDict

from data.db import dsn
from data.migrate import migrate
from agents import llm
from schemas.enums import AgentRole


class Toy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: str


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def experiment_id(conn):
    row = conn.execute("SELECT id FROM experiments WHERE name = 'EXP-001'").fetchone()
    if row is None:
        pytest.skip("EXP-001 not present in dev DB")
    return row[0]


@pytest.fixture
def run_cleanup(conn):
    """structured_call commits its AgentRun rows; delete the ones each test
    creates so the dev DB stays clean."""
    before = conn.execute("SELECT coalesce(max(id), 0) FROM agent_runs").fetchone()[0]
    yield
    conn.execute("DELETE FROM agent_runs WHERE id > %s", (before,))
    conn.commit()


class FakeResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": self._content}}


def test_native_request_shape_and_parse(conn, experiment_id, run_cleanup, monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured.update(url=url, payload=json)
        return FakeResponse('{"color": "blue"}')

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    out = llm.structured_call(
        conn, role=AgentRole.EVIDENCE_COLLECTOR, experiment_id=experiment_id,
        system_prompt="s", user_prompt="u", schema=Toy)
    assert out.color == "blue"
    assert captured["url"].endswith("/api/chat")
    assert "/v1/" not in captured["url"]
    payload = captured["payload"]
    assert payload["stream"] is False
    assert payload["format"] == Toy.model_json_schema()
    assert payload["options"]["num_ctx"] == llm.LLM_CFG["num_ctx"]
    assert payload["options"]["temperature"] == 0.2
    assert "temperature" not in payload  # moved into options on native API


def test_invalid_json_gets_corrective_retry(conn, experiment_id, run_cleanup, monkeypatch):
    responses = iter(["not json at all", '{"color": "red"}'])
    payloads = []

    def fake_post(url, json=None, timeout=None):
        payloads.append(json)
        return FakeResponse(next(responses))

    monkeypatch.setattr(llm.httpx, "post", fake_post)
    out = llm.structured_call(
        conn, role=AgentRole.EVIDENCE_COLLECTOR, experiment_id=experiment_id,
        system_prompt="s", user_prompt="u", schema=Toy)
    assert out.color == "red"
    # second request carries the bad output + a corrective user turn
    assert len(payloads) == 2
    roles = [m["role"] for m in payloads[1]["messages"]]
    assert roles[-2:] == ["assistant", "user"]
    assert "corrected JSON" in payloads[1]["messages"][-1]["content"]


def test_exhausted_retries_raise_and_log_failure(conn, experiment_id, run_cleanup, monkeypatch):
    monkeypatch.setattr(llm.httpx, "post",
                        lambda url, json=None, timeout=None: FakeResponse("nope"))
    with pytest.raises(llm.ModelOutputRejected):
        llm.structured_call(
            conn, role=AgentRole.EVIDENCE_COLLECTOR, experiment_id=experiment_id,
            system_prompt="s", user_prompt="u", schema=Toy)
    row = conn.execute(
        "SELECT success FROM agent_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert row == (False,)


def test_format_schema_strips_string_maxlength_only():
    """llama.cpp's grammar compiler chokes on large string maxLength
    ("failed to parse grammar", 2026-07-29 — SectionDraft's 25000-char
    runaway guard). The `format` schema drops it; maxItems (list caps, used
    by the funnel schemas) stays; Pydantic still validates the full schema."""
    from pydantic import BaseModel, Field

    class Guarded(BaseModel):
        text: str = Field(max_length=25000)
        items: list[str] = Field(default_factory=list, max_length=3)

    stripped = llm.format_schema(Guarded)
    assert "maxLength" not in stripped["properties"]["text"]
    assert stripped["properties"]["items"]["maxItems"] == 3
    # the model's own schema is untouched (never mutate a cached schema)
    assert Guarded.model_json_schema()["properties"]["text"]["maxLength"] == 25000
