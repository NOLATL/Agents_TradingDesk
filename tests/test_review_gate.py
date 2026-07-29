"""Review-gate precheck hook (2026-07-29): deterministic issues regenerate
locally without consuming a Sonnet attempt, precheck exhaustion still reaches
the reviewer (never a silent drop), the kind-specific system rubric and
callable review context pass through, transient API failures retry instead
of ending the night, and every verdict fires on_review. No network — review
is mocked.
"""

import anthropic
import httpx

from research import review_gate


def status_error(status: int) -> anthropic.APIStatusError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIStatusError(
        f"status {status}", response=httpx.Response(status, request=req),
        body=None)


def approve_verdict(*_a, **_k):
    return review_gate.ReviewVerdict(approve=True, critique="ok")


def test_precheck_failures_do_not_consume_review_attempts(monkeypatch):
    review_calls = []

    def fake_review(kind, artifact, context, **kwargs):
        review_calls.append(artifact)
        return approve_verdict()

    monkeypatch.setattr(review_gate, "review", fake_review)

    generated = []

    def generate(critique):
        generated.append(critique)
        return f"draft-{len(generated)}"

    prechecks = iter(["fix the ticker", None])

    artifact, approved, _ = review_gate.generate_with_review(
        generate, kind="test", context="ctx",
        precheck=lambda a: next(prechecks),
    )
    assert approved and artifact == "draft-2"
    # one failed precheck → one local regen with the issue text, ONE review call
    assert generated == [None, "fix the ticker"]
    assert review_calls == ["draft-2"]


def test_precheck_exhaustion_still_reaches_reviewer(monkeypatch):
    review_calls = []

    def fake_review(kind, artifact, context, **kwargs):
        review_calls.append(artifact)
        return review_gate.ReviewVerdict(approve=False, critique="bad anyway")

    monkeypatch.setattr(review_gate, "review", fake_review)
    monkeypatch.setattr(review_gate, "GATE_RETRIES", 0)

    artifact, approved, critique = review_gate.generate_with_review(
        lambda c: "stubborn-draft", kind="test", context="ctx",
        precheck=lambda a: "still broken", max_precheck_retries=2,
    )
    assert not approved and critique == "bad anyway"
    assert review_calls == ["stubborn-draft"]  # sent despite failing precheck


def test_transient_529_is_retried_then_succeeds(monkeypatch):
    """The 2026-07-29 test-drive regression: one Anthropic 529 must not end
    the night as a rejection."""
    calls = []
    outcomes = iter([status_error(529), status_error(529), "approve"])

    def flaky_review(kind, artifact, context, **kwargs):
        calls.append(artifact)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return approve_verdict()

    monkeypatch.setattr(review_gate, "review", flaky_review)
    monkeypatch.setattr(review_gate.time, "sleep", lambda s: None)

    _, approved, _ = review_gate.generate_with_review(
        lambda c: "draft", kind="test", context="ctx")
    assert approved and len(calls) == 3


def test_auth_errors_are_not_retried(monkeypatch):
    calls = []

    def auth_fail(kind, artifact, context, **kwargs):
        calls.append(artifact)
        raise status_error(401)

    monkeypatch.setattr(review_gate, "review", auth_fail)
    monkeypatch.setattr(review_gate.time, "sleep", lambda s: None)

    _, approved, critique = review_gate.generate_with_review(
        lambda c: "draft", kind="test", context="ctx")
    assert not approved and "review gate error" in critique
    assert len(calls) == 1  # 4xx = config problem, retrying is pointless


def test_on_review_fires_per_verdict(monkeypatch):
    verdicts = iter([
        review_gate.ReviewVerdict(approve=False, critique="fix the mechanism"),
        review_gate.ReviewVerdict(approve=True, critique="ok now"),
    ])
    monkeypatch.setattr(review_gate, "review", lambda *a, **k: next(verdicts))

    seen = []
    _, approved, _ = review_gate.generate_with_review(
        lambda c: "draft", kind="test", context="ctx",
        on_review=lambda ok, crit: seen.append((ok, crit)))
    assert approved
    assert seen == [(False, "fix the mechanism"), (True, "ok now")]


def test_system_and_callable_context_pass_through(monkeypatch):
    seen = {}

    def fake_review(kind, artifact, context, *, system=None, context_cap=None):
        seen.update(kind=kind, context=context, system=system,
                    context_cap=context_cap)
        return approve_verdict()

    monkeypatch.setattr(review_gate, "review", fake_review)

    state = {"extra": ""}

    def context_fn():
        return "base" + state["extra"]

    def generate(critique):
        state["extra"] = " + symbol identities"  # set during generation
        return "draft"

    _, approved, _ = review_gate.generate_with_review(
        generate, kind="k", context=context_fn,
        system="SCOUT RUBRIC", context_cap=40000,
    )
    assert approved
    assert seen["context"] == "base + symbol identities"  # evaluated at review time
    assert seen["system"] == "SCOUT RUBRIC"
    assert seen["context_cap"] == 40000
