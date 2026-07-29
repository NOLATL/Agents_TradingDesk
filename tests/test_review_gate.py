"""Review-gate precheck hook (2026-07-29): deterministic issues regenerate
locally without consuming a Sonnet attempt, precheck exhaustion still reaches
the reviewer (never a silent drop), and the kind-specific system rubric and
callable review context pass through. No network — review is mocked.
"""

from research import review_gate


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
