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


# --- conviction-seeking loop (2026-08-07 scale-up) -------------------------

def cv(approve: bool, conviction: str = "low", critique: str = "c"):
    return review_gate.ConvictionVerdict(
        approve=approve, conviction=conviction, critique=critique)


def counting_generate(drafts: list):
    def generate(feedback):
        drafts.append(feedback)
        return f"draft-{len(drafts)}"
    return generate


def test_conviction_medium_publishes_on_first_turn(monkeypatch):
    monkeypatch.setattr(review_gate, "review",
                        lambda *a, **k: cv(True, "medium", "solid"))
    drafts = []
    result = review_gate.generate_with_conviction_review(
        counting_generate(drafts), kind="t", context="ctx")
    assert result.approved and not result.fallback
    assert result.conviction == "medium" and result.turns == 1
    assert result.artifact == "draft-1"
    assert drafts == [None]


def test_approved_low_banks_then_medium_publishes(monkeypatch):
    verdicts = iter([cv(True, "low", "thin — name the filers"),
                     cv(True, "high", "strong")])
    monkeypatch.setattr(review_gate, "review", lambda *a, **k: next(verdicts))
    drafts, stronger = [], []

    def improve(verdict):
        stronger.append(verdict.critique)
        return f"SEEK STRONGER: {verdict.critique}"

    result = review_gate.generate_with_conviction_review(
        counting_generate(drafts), kind="t", context="ctx",
        improve_feedback=improve)
    assert result.approved and not result.fallback
    assert result.conviction == "high" and result.turns == 2
    assert result.artifact == "draft-2"
    # turn 2's feedback came from improve_feedback, not the rejection framing
    assert drafts == [None, "SEEK STRONGER: thin — name the filers"]
    assert stronger == ["thin — name the filers"]


def test_exhaustion_publishes_first_banked_low(monkeypatch):
    monkeypatch.setattr(review_gate, "review",
                        lambda *a, **k: cv(True, "low", "still thin"))
    drafts = []
    result = review_gate.generate_with_conviction_review(
        counting_generate(drafts), kind="t", context="ctx", max_turns=6,
        improve_feedback=lambda v: "STRONGER")
    assert result.approved and result.fallback
    assert result.conviction == "low" and result.turns == 6
    assert result.artifact == "draft-1"  # FIRST approved-low, not the last
    assert len(drafts) == 6


def test_zero_approvals_exhaustion_rejects(monkeypatch):
    monkeypatch.setattr(review_gate, "review",
                        lambda *a, **k: cv(False, "low", "ungrounded"))
    drafts = []
    result = review_gate.generate_with_conviction_review(
        counting_generate(drafts), kind="t", context="ctx", max_turns=3)
    assert not result.approved and not result.fallback
    assert result.critique == "ungrounded" and result.turns == 3
    # rejection turns feed the framed critique back to the generator
    assert "REJECTED BY REVIEW" in drafts[1] and "ungrounded" in drafts[1]


def test_precheck_regens_do_not_consume_turns(monkeypatch):
    review_calls = []

    def fake_review(kind, artifact, context, **kwargs):
        review_calls.append(artifact)
        return cv(True, "medium")

    monkeypatch.setattr(review_gate, "review", fake_review)
    drafts = []
    prechecks = iter(["fix ticker", "fix citation", None])
    result = review_gate.generate_with_conviction_review(
        counting_generate(drafts), kind="t", context="ctx",
        precheck=lambda a: next(prechecks), max_precheck_retries=3)
    assert result.approved and result.turns == 1
    assert drafts == [None, "fix ticker", "fix citation"]  # free local regens
    assert review_calls == ["draft-3"]  # exactly ONE Sonnet call


def test_gate_failure_mid_loop_publishes_banked(monkeypatch):
    outcomes = iter([cv(True, "low", "thin"), status_error(529),
                     status_error(529), status_error(529)])

    def flaky_review(kind, artifact, context, **kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(review_gate, "review", flaky_review)
    monkeypatch.setattr(review_gate.time, "sleep", lambda s: None)
    result = review_gate.generate_with_conviction_review(
        counting_generate([]), kind="t", context="ctx",
        improve_feedback=lambda v: "STRONGER")
    # turn 2's gate died after retries — the banked approved draft publishes
    assert result.approved and result.fallback
    assert result.artifact == "draft-1"
    assert "gate failed" in result.critique


def test_conviction_loop_passes_tail_verdict_and_cache(monkeypatch):
    seen = {}

    def fake_review(kind, artifact, context, *, system=None, context_cap=None,
                    context_tail="", output_format=None, cache=False):
        seen.update(context=context, context_tail=context_tail,
                    output_format=output_format, cache=cache)
        return cv(True, "medium")

    monkeypatch.setattr(review_gate, "review", fake_review)
    result = review_gate.generate_with_conviction_review(
        lambda f: "draft", kind="t", context="stable ctx",
        context_tail=lambda: "per-turn identities")
    assert result.approved
    assert seen["context"] == "stable ctx"
    assert seen["context_tail"] == "per-turn identities"
    assert seen["output_format"] is review_gate.ConvictionVerdict
    assert seen["cache"] is True


def test_on_review_receives_conviction(monkeypatch):
    verdicts = iter([cv(False, "low", "bad"), cv(True, "medium", "ok")])
    monkeypatch.setattr(review_gate, "review", lambda *a, **k: next(verdicts))
    seen = []
    review_gate.generate_with_conviction_review(
        lambda f: "draft", kind="t", context="ctx",
        on_review=lambda ok, crit, conv: seen.append((ok, crit, conv)))
    assert seen == [(False, "bad", "low"), (True, "ok", "medium")]
