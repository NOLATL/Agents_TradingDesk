"""Gate A blinding tests (invariant 5): the boundary is explicit code with its

own tests, not a convention. Network/LLM-free."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from agents.roles.evidence import BlindingViolation, build_analyst_context, is_price_contaminated
from agents.state import Claim, ComputedMetric, EvidencePacket

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def make_metric(name: str, quarantined: bool = False) -> ComputedMetric:
    return ComputedMetric(
        assertion_id=1, symbol="ETN", name=name, value=Decimal("0.4"),
        function_name=f"analytics.test.{name}", code_version="test",
        quarantined_from_analyst=quarantined,
    )


def make_claim(claim: str, quote: str = "quote") -> Claim:
    return Claim(
        assertion_id=2, document_id=1, content_hash="h", symbol="ETN",
        quote=quote, claim=claim,
    )


def make_packet(**overrides) -> EvidencePacket:
    base = dict(
        theme="grid-constraint", experiment_id=1, simulation_clock=NOW,
        symbols=["ETN"], claims=[make_claim("Backlog grew to $11.2B in fiscal 2025.")],
        metrics=[make_metric("exposure_score")], quarantined_metrics=[], macro=[],
    )
    base.update(overrides)
    return EvidencePacket(**base)


def test_clean_packet_builds_context():
    context = build_analyst_context(make_packet())
    assert "exposure_score" in context and "Backlog grew" in context


def test_quarantined_metric_in_visible_list_raises():
    packet = make_packet(metrics=[make_metric("volatility_1y", quarantined=True)])
    with pytest.raises(BlindingViolation):
        build_analyst_context(packet)


def test_momentum_named_metric_raises_even_unflagged():
    """Defense in depth: a price-derived metric mislabeled as non-quarantined

    still cannot pass the boundary."""
    packet = make_packet(metrics=[make_metric("momentum_12_1", quarantined=False)])
    with pytest.raises(BlindingViolation):
        build_analyst_context(packet)


def test_price_performance_claims_never_reach_analyst():
    packet = make_packet(
        claims=[
            make_claim("Backlog grew to $11.2B."),
            make_claim("The stock price rose 40% over the year.", quote="stock price rose 40%"),
        ]
    )
    context = build_analyst_context(packet)
    assert "Backlog grew" in context
    assert "40%" not in context and "stock price" not in context.lower()


def test_quarantined_metrics_do_not_change_analyst_context():
    """Gate A: removing price data does not change what the analyst sees —

    the context is bit-identical with and without quarantined metrics, so the
    basket construction input is provably price-free."""
    with_price = make_packet(
        quarantined_metrics=[make_metric("momentum_12_1", quarantined=True)]
    )
    without_price = make_packet(quarantined_metrics=[])
    assert build_analyst_context(with_price) == build_analyst_context(without_price)


def test_price_contamination_detector():
    assert is_price_contaminated("total shareholder return of 22%")
    assert is_price_contaminated("the 52-week high")
    assert not is_price_contaminated("segment revenue rose on data center demand")
