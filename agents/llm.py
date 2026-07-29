"""Local inference wrapper (Layer 1).

- Ollama OpenAI-compatible endpoint; model tag pinned from config (must match
  EXPERIMENT_REGISTRY.md).
- Every call logs an AgentRun row: role, model tag+digest, prompt_version
  (sha256 of the prompt template), input/output hashes, success/error.
- Structured outputs: the response must validate against a Pydantic schema.
  Parse-and-retry handles Ollama's JSON-in-content leakage; after max_retries
  the call FAILS — malformed output is rejected, never massaged (a structured
  hallucination is still a hallucination; here we only enforce structure).
- num_ctx raised above the 32K default so filing-length inputs don't silently
  truncate; very long documents are chunked upstream (chunking.py).
"""

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from config.loader import SLICE
from data import repository as repo
from schemas.enums import AgentRole
from schemas.models import AgentRun

S = TypeVar("S", bound=BaseModel)

LLM_CFG = SLICE["llm"]
# Native Ollama API root (2026-07-29): the OpenAI-compat /v1 endpoint
# silently ignores options.num_ctx (DECISIONS.md), so calls go to /api/chat
# where num_ctx is honored and `format` gives schema-constrained decoding.
NATIVE_ROOT = LLM_CFG["base_url"].removesuffix("/v1")


class ModelOutputRejected(Exception):
    """Raised when the model cannot produce schema-valid output within the

    retry budget. Callers treat this as the model-quality breaker does:
    reject the proposal; nothing downstream sees the malformed output."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def model_digest(tag: str) -> str:
    """Ollama image digest for the pinned tag (logged per run)."""
    try:
        out = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, timeout=10
        ).stdout
        for line in out.splitlines():
            if line.startswith(tag):
                return line.split()[1]
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


_DIGESTS: dict[str, str] = {}


def cached_digest(tag: str | None = None) -> str:
    tag = tag or LLM_CFG["model_tag"]
    if tag not in _DIGESTS:
        _DIGESTS[tag] = model_digest(tag)
    return _DIGESTS[tag]


def _extract_json(content: str) -> str:
    """Ollama sometimes wraps JSON in prose/code fences; extract the first

    top-level JSON object. Extraction, not correction — the JSON itself is
    never edited."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start = content.find("{")
    if start == -1:
        return content
    depth = 0
    for i, ch in enumerate(content[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    return content[start:]


def structured_call(
    conn,
    *,
    role: AgentRole,
    experiment_id: int,
    system_prompt: str,
    user_prompt: str,
    schema: type[S],
    temperature: float = 0.2,
    model_tag: str | None = None,
) -> S:
    """One schema-validated LLM call, fully logged to agent_runs.

    model_tag overrides the pinned engine model for research-layer callers
    (e.g. the scout, per the 2026-07-29 ruling); the four engine roles never
    pass it and keep the frozen tag."""
    tag = model_tag or LLM_CFG["model_tag"]
    run = AgentRun(
        experiment_id=experiment_id,
        role=role,
        model_tag=tag + "@" + cached_digest(tag),
        prompt_version=_sha(system_prompt),
        input_hash=_sha(system_prompt + "\x00" + user_prompt),
        started_at=datetime.now(timezone.utc),
    )
    repo.insert(conn, run)
    conn.commit()  # the run row survives even if the call crashes

    schema_hint = json.dumps(schema.model_json_schema(), indent=None)
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"{user_prompt}\n\n"
                f"Respond with ONLY a JSON object valid against this JSON Schema "
                f"(no prose, no markdown):\n{schema_hint}"
            ),
        },
    ]

    last_error: Exception | None = None
    for _ in range(LLM_CFG["max_retries"]):
        response = httpx.post(
            f"{NATIVE_ROOT}/api/chat",
            json={
                "model": tag,
                "messages": messages,
                "stream": False,
                "format": schema.model_json_schema(),
                "options": {
                    "temperature": temperature,
                    "num_ctx": LLM_CFG["num_ctx"],
                },
            },
            timeout=600.0,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        try:
            result = schema.model_validate_json(_extract_json(content))
            _finish(conn, run, output=content, success=True)
            return result
        except (ValidationError, json.JSONDecodeError) as e:
            last_error = e
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": f"Invalid: {e}. Return ONLY the corrected JSON object.",
                }
            )

    _finish(conn, run, output=str(last_error), success=False)
    raise ModelOutputRejected(
        f"{role.value}: no schema-valid output in {LLM_CFG['max_retries']} attempts: {last_error}"
    )


def _finish(conn, run: AgentRun, *, output: str, success: bool) -> None:
    conn.execute(
        "UPDATE agent_runs SET finished_at = %s, output_hash = %s, success = %s, error = %s"
        " WHERE id = %s",
        (
            datetime.now(timezone.utc),
            _sha(output),
            success,
            None if success else output[:2000],
            run.id,
        ),
    )
    conn.commit()
