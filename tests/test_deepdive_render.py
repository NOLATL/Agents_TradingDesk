"""Deep-dive report renderer: research-report sections rehydrate from
Postgres alone (manifest first, timestamp/id-window fallback), degrade loudly
instead of raising (a render exception would fail the weekly cycle after
committed LLM work), and every assertion id used resolves in the provenance
appendix.
"""

import json
import re
from datetime import timedelta

import psycopg
import pytest

from agents.state import ComputedMetric, EvidencePacket, MacroPoint
from agents.state import Claim as PacketClaim
from agents import run_slice
from data import repository as repo
from data.db import dsn
from data.migrate import migrate
from ops import approve
from reporting import deepdive
from schemas.enums import (
    AgentRole,
    MilestoneStatus,
    ProvenanceType,
    ThesisStatus,
)
from schemas.models import (
    Assertion,
    Document,
    Forecast,
    Milestone,
    Thesis,
)
from tests.test_theme_gate import EXIT_CRITERIA, NOW, propose

WEEK = "2026-W30"


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _rollback_everything(conn):
    with conn.transaction(force_rollback=True):
        conn.execute(
            "UPDATE themes SET accepted_on = NULL WHERE accepted_on = %s",
            (approve.today_et(),),
        )
        yield


def _doc(conn, slug: str) -> Document:
    doc = Document(
        content_hash=f"hash-{slug}",
        raw_bytes=b"x",
        retrieval_url=f"https://example.test/{slug}",
        source_name="test",
        ingestion_time=NOW,
        first_eligible_decision_time=NOW,
    )
    repo.insert_document(conn, doc)
    return doc


def _thesis(conn, theme, *, status, version=1, parent=None,
            basket="[]", do_nothing=None) -> Thesis:
    exp_id = conn.execute(
        "SELECT id FROM experiments WHERE name = 'EXP-001'"
    ).fetchone()[0]
    thesis = Thesis(
        experiment_id=exp_id, title=f"thesis for {theme.slug}",
        strategy_tag=theme.slug, theme_id=theme.id, status=status,
        version=version, parent_version_id=parent,
        causal_chain="A drives B drives C", exposure_basket_approach=basket,
        valuation_check="valuation prose", exit_criteria=EXIT_CRITERIA,
        do_nothing_case=do_nothing, model_tag="test", prompt_version="test",
        created_at=NOW,
    )
    repo.insert(conn, thesis)
    return thesis


def _judgment(conn, value: dict) -> Assertion:
    a = Assertion(type=ProvenanceType.JUDGMENT, value=value,
                  model_tag="test", prompt_version="test", created_at=NOW)
    repo.insert_assertion(conn, a)
    return a


def _dive(conn, slug: str, *, status=ThesisStatus.REVIEWED,
          justification="exposure argument", claim_text="AAA grew backlog"):
    """Full synthetic dive matching the real run's persistence pattern:
    claims (wall clock ≥ run clock) → metrics/macro (== run clock) →
    analyst draft JUDGMENT → v2 + audit + summary."""
    theme = propose(conn, slug)
    doc = _doc(conn, slug)
    claim = Assertion(
        type=ProvenanceType.SOURCE_FACT,
        value={"symbol": "AAA", "claim": claim_text, "figure_name": None,
               "figure_value": None, "figure_unit": None},
        source_doc_id=doc.id, citation_span="verbatim | span",
        model_tag="test", prompt_version="test",
        created_at=NOW + timedelta(seconds=5),
    )
    repo.insert_assertion(conn, claim)
    metric = Assertion(
        type=ProvenanceType.COMPUTED,
        value={"symbol": "AAA", "metric": "exposure_score", "value": 0.9,
               "detail": {}, "quarantined_from_analyst": False},
        function_name="analytics.exposure.exposure_score", code_version="test",
        created_at=NOW,
    )
    repo.insert_assertion(conn, metric)
    quarantined = Assertion(
        type=ProvenanceType.COMPUTED,
        value={"symbol": "AAA", "metric": "momentum_12_1", "value": 0.7,
               "detail": {}, "quarantined_from_analyst": True},
        function_name="analytics.momentum.momentum_12_1", code_version="test",
        created_at=NOW,
    )
    repo.insert_assertion(conn, quarantined)
    macro = Assertion(
        type=ProvenanceType.SOURCE_FACT,
        value={"series_id": "DGS10", "date": "2026-06-30", "value": 4.2},
        source_doc_id=doc.id, citation_span="DGS10@2026-06-30",
        created_at=NOW,
    )
    repo.insert_assertion(conn, macro)
    macro2 = Assertion(
        type=ProvenanceType.SOURCE_FACT,
        value={"series_id": "DGS10", "date": "2026-05-31", "value": 4.0},
        source_doc_id=doc.id, citation_span="DGS10@2026-05-31",
        created_at=NOW,
    )
    repo.insert_assertion(conn, macro2)

    basket = json.dumps([{
        "symbol": "AAA", "rank": 1, "justification": justification,
        "supporting_assertion_ids": [metric.id, claim.id],
    }])
    v1 = _thesis(conn, theme, status=ThesisStatus.DRAFT, basket=basket)
    _judgment(conn, {"thesis_id": v1.id, "version": 1,
                     "kind": "analyst_thesis_draft"})
    v2 = _thesis(conn, theme, status=status, version=2, parent=v1.id,
                 basket=basket, do_nothing="do-nothing prose")
    _judgment(conn, {"thesis_id": v2.id, "kind": "reviewer_audit",
                     "verdict": "ACTIVATE" if status is ThesisStatus.REVIEWED
                     else "REJECT",
                     "challenges": ["challenge one", "challenge two",
                                    "challenge three"],
                     "price_narrative_check": "price-narrative prose"})
    _judgment(conn, {"thesis_id": v2.id, "kind": "report_executive_summary",
                     "text": "executive summary prose"})
    for thesis_id, p in ((v1.id, 0.60), (v2.id, 0.45)):
        repo.insert(conn, Forecast(
            thesis_id=thesis_id, statement="AAA backlog > $1B",
            probability=p, horizon_end=NOW.date(),
            resolution_criterion="Q4 report", created_by=AgentRole.RESEARCH_ANALYST,
            model_tag="test", prompt_version="test", created_at=NOW,
        ))
    repo.insert(conn, Milestone(
        thesis_id=v2.id, description="capex stays elevated",
        falsification_criterion="two guidance cuts",
        status=MilestoneStatus.PENDING, created_at=NOW,
    ))
    return v2, claim, metric, quarantined, macro


def test_review_section_always_rendered_on_reviewed(conn):
    with conn.transaction():
        v2, *_ = _dive(conn, "dd-reviewed")
        out = deepdive.render(conn, v2, WEEK, None)
        assert "## Independent review & risk" in out
        assert "challenge one" in out
        assert "price-narrative prose" in out
        assert "momentum_12_1" in out  # quarantined table, labeled
        assert "reviewer-only" in out
        raise psycopg.Rollback


def test_reviewed_decision_commands_preserved(conn):
    with conn.transaction():
        v2, *_ = _dive(conn, "dd-decide")
        out = deepdive.render(conn, v2, WEEK, None)
        assert f"uv run python -m ops.approve thesis accept {v2.id}" in out
        assert "REVIEWER REJECTED" not in out
        raise psycopg.Rollback


def test_rejected_banner_and_no_decision_block(conn):
    with conn.transaction():
        v2, *_ = _dive(conn, "dd-rejected", status=ThesisStatus.REJECTED)
        out = deepdive.render(conn, v2, WEEK, None)
        assert "REVIEWER REJECTED this draft" in out
        assert "theme reject dd-rejected" in out
        assert "thesis accept" not in out
        assert "challenge one" in out  # still fully reviewable
        raise psycopg.Rollback


def test_forecast_table_shows_pre_and_post_review_probability(conn):
    with conn.transaction():
        v2, *_ = _dive(conn, "dd-probs")
        out = deepdive.render(conn, v2, WEEK, None)
        assert "| 0.60 | 0.45 |" in out
        raise psycopg.Rollback


def test_milestones_fall_back_to_parent_version(conn):
    with conn.transaction():
        v2, *_ = _dive(conn, "dd-msfall")
        conn.execute("DELETE FROM milestones WHERE thesis_id = %s", (v2.id,))
        repo.insert(conn, Milestone(
            thesis_id=v2.parent_version_id, description="v1-only milestone",
            falsification_criterion="crit", status=MilestoneStatus.PENDING,
            created_at=NOW,
        ))
        out = deepdive.render(conn, v2, WEEK, None)
        assert "v1-only milestone" in out
        assert "monitoring has nothing falsifiable" not in out
        raise psycopg.Rollback


def test_every_assertion_ref_resolves_in_appendix(conn):
    with conn.transaction():
        v2, *_ = _dive(conn, "dd-refs")
        out = deepdive.render(conn, v2, WEEK, None)
        body, appendix = out.split("## Provenance appendix")
        resolved = set(re.findall(r"^\[\^A(\d+)\]:", appendix, re.MULTILINE))
        used = set(re.findall(r"\[\^A(\d+)\](?!:)", body))
        assert used and used <= resolved
        raise psycopg.Rollback


def test_missing_assertion_renders_provenance_break(conn):
    with conn.transaction():
        theme = propose(conn, "dd-break")
        basket = json.dumps([{"symbol": "AAA", "rank": 1,
                              "justification": "j",
                              "supporting_assertion_ids": [999999999]}])
        v2 = _thesis(conn, theme, status=ThesisStatus.REVIEWED, basket=basket)
        out = deepdive.render(conn, v2, WEEK, None)
        assert "MISSING — provenance break" in out
        raise psycopg.Rollback


def test_render_is_db_read_only(conn):
    with conn.transaction():
        v2, *_ = _dive(conn, "dd-readonly")
        counts = lambda: conn.execute(  # noqa: E731
            "SELECT (SELECT count(*) FROM assertions),"
            " (SELECT count(*) FROM theses), (SELECT count(*) FROM forecasts)"
        ).fetchone()
        before = counts()
        deepdive.render(conn, v2, WEEK, None)
        assert counts() == before
        raise psycopg.Rollback


def test_table_cells_escape_pipes_and_newlines(conn):
    with conn.transaction():
        v2, *_ = _dive(conn, "dd-pipes",
                       justification="metric|driven\nargument")
        out = deepdive.render(conn, v2, WEEK, None)
        assert "metric\\|driven argument" in out
        # appendix is footnote definitions now: single-line defs, and a
        # pipe-bearing verbatim quote must never start a markdown table there
        appendix = out.split("## Provenance appendix")[1]
        assert not [ln for ln in appendix.splitlines() if ln.startswith("|")]
        assert all(ln.startswith(("[^A", ">", "#", "Every", ""))
                   for ln in appendix.splitlines())
        raise psycopg.Rollback


def test_degrades_loudly_without_audit_summary_or_evidence(conn):
    with conn.transaction():
        theme = propose(conn, "dd-bare")
        bare = _thesis(conn, theme, status=ThesisStatus.REVIEWED)
        out = deepdive.render(conn, bare, WEEK, None)  # must not raise
        assert "(no executive summary recorded for this dive)" in out
        assert "(no reviewer audit recoverable for this dive)" in out
        assert "pre-upgrade artifact" in out
        raise psycopg.Rollback


def test_manifest_is_preferred_over_window_closure(conn):
    with conn.transaction():
        v2, claim, metric, quarantined, macro = _dive(conn, "dd-manifest")
        # a claim OUTSIDE the window closure (id above the draft anchor) that
        # only the manifest lists — it can render only via the manifest path
        extra = Assertion(
            type=ProvenanceType.SOURCE_FACT,
            value={"symbol": "AAA", "claim": "claim only the manifest lists"},
            source_doc_id=claim.source_doc_id, citation_span="extra",
            model_tag="test", prompt_version="test",
            created_at=NOW + timedelta(seconds=6),
        )
        repo.insert_assertion(conn, extra)
        repo.insert_assertion(conn, Assertion(
            type=ProvenanceType.COMPUTED,
            value={"kind": "evidence_packet_manifest", "thesis_id": v2.id,
                   "thesis_v1_id": v2.parent_version_id,
                   "claim_assertion_ids": [claim.id, extra.id],
                   "metric_assertion_ids": [metric.id, quarantined.id],
                   "macro_assertion_ids": [macro.id]},
            function_name="agents.run_slice.packet_manifest",
            code_version="test", created_at=NOW,
        ))
        out = deepdive.render(conn, v2, WEEK, None)
        assert "AAA grew backlog" in out
        assert "claim only the manifest lists" in out
        raise psycopg.Rollback


def _narrative(conn, v2, claim_id, *, dropped=(), body_words=5000,
               audit="approved", **sections):
    base = {
        "lead": "Verdict first: the reviewer activated this thesis.",
        "thesis_narrative": f"The chain holds on backlog. [^A{claim_id}]",
        "macro_narrative": None,
        "company_narratives": [
            {"symbol": "AAA",
             "narrative": f"AAA anchors the basket on backlog. [^A{claim_id}]"}],
        "valuation_discussion": None,
        "forecast_rationale": None,
        "risk_discussion": None,
        "monitoring_outlook": None,
    }
    base.update(sections)
    _judgment(conn, {"thesis_id": v2.id, "kind": "report_narrative",
                     "narrative": base, "dropped_sections": list(dropped),
                     "audit": audit, "body_words": body_words})


def test_narrative_sections_render_with_resolving_markers(conn):
    with conn.transaction():
        v2, claim, *_ = _dive(conn, "dd-nar")
        _narrative(conn, v2, claim.id)
        out = deepdive.render(conn, v2, WEEK, None)
        assert "Verdict first: the reviewer activated" in out
        assert "The chain holds on backlog." in out
        assert "AAA anchors the basket on backlog." in out
        assert f"[^A{claim.id}]:" in out  # marker resolves as footnote def
        assert "Narrative unavailable for AAA" not in out
        assert "Raw causal chain (analyst JUDGMENT)" in out
        raise psycopg.Rollback


def test_absent_narrative_falls_back_wholesale(conn):
    with conn.transaction():
        v2, claim, *_ = _dive(conn, "dd-nonar")
        out = deepdive.render(conn, v2, WEEK, None)
        assert "no narrative recorded for this dive" in out
        assert "Evidence on file (SOURCE_FACT):" in out
        assert "AAA grew backlog" in out
        raise psycopg.Rollback


def test_missing_company_narrative_falls_back_per_symbol(conn):
    with conn.transaction():
        v2, claim, *_ = _dive(conn, "dd-partial")
        _narrative(conn, v2, claim.id, company_narratives=[])
        out = deepdive.render(conn, v2, WEEK, None)
        assert "Narrative unavailable for AAA" in out
        assert "Evidence on file (SOURCE_FACT):" in out
        assert "The chain holds on backlog." in out  # other sections intact
        raise psycopg.Rollback


def test_dropped_sections_render_loud_note(conn):
    with conn.transaction():
        v2, claim, *_ = _dive(conn, "dd-dropped")
        _narrative(conn, v2, claim.id, dropped=["risk_discussion"])
        out = deepdive.render(conn, v2, WEEK, None)
        assert "narrative section 'risk_discussion' was dropped" in out
        raise psycopg.Rollback


def test_low_body_words_renders_loud_note(conn):
    """Dynamic floor (2026-08-07): sum of guard section minimums over the
    sections PRESENT — here lead(150) + thesis(600) + 1 company(300) = 1050."""
    with conn.transaction():
        v2, claim, *_ = _dive(conn, "dd-thin")
        _narrative(conn, v2, claim.id, body_words=100)
        out = deepdive.render(conn, v2, WEEK, None)
        assert "below the 1050-word floor" in out
        raise psycopg.Rollback


def test_body_words_floor_scales_with_surviving_sections(conn):
    """A healthy small narrative must NOT warn (the fixed 4500 target would
    have false-alarmed here); dropped/absent sections don't inflate the bar."""
    with conn.transaction():
        v2, claim, *_ = _dive(conn, "dd-healthy-small")
        _narrative(conn, v2, claim.id, body_words=1100)
        out = deepdive.render(conn, v2, WEEK, None)
        assert "word floor" not in out and "word target" not in out
        raise psycopg.Rollback


def test_narrative_words_floor_helper():
    from reporting.deepdive import _narrative_words_floor
    assert _narrative_words_floor({}) == 0
    full = {"lead": "x", "thesis_narrative": "x", "macro_narrative": "x",
            "valuation_discussion": "x", "forecast_rationale": "x",
            "risk_discussion": "x", "monitoring_outlook": "x",
            "company_narratives": [
                {"symbol": s, "narrative": "x"} for s in
                ("A", "B", "C", "D", "E", "F", "G")]}
    # 150+600+400+300+400+300+200 + 7×300 = 4450 (≈ the old fixed 4500
    # for a seven-name basket — the constant was implicitly this shape)
    assert _narrative_words_floor(full) == 4450
    # audit-dropped sections (None / removed) lower the floor
    partial = {**full, "risk_discussion": None, "monitoring_outlook": None,
               "company_narratives": full["company_narratives"][:6]}
    assert _narrative_words_floor(partial) == 4450 - 300 - 200 - 300


def test_scoreboard_sparkline_and_mermaid(conn):
    with conn.transaction():
        v2, *_ = _dive(conn, "dd-visual")
        out = deepdive.render(conn, v2, WEEK, None)
        assert "## Exposure scoreboard" in out
        # old per-company metric mini-table header is gone
        assert "| Assertion | Metric | Value | Function (version) |" not in out
        assert "```mermaid" in out and "xychart-beta" in out
        assert any(g in out for g in "▁▂▃▄▅▆▇█")  # macro trend sparkline
        assert "▰" in out  # exposure bar
        raise psycopg.Rollback


def test_run_slice_manifest_shape(conn):
    with conn.transaction():
        theme = propose(conn, "dd-mshape")
        v1 = _thesis(conn, theme, status=ThesisStatus.DRAFT)
        v2 = _thesis(conn, theme, status=ThesisStatus.REVIEWED, version=2,
                     parent=v1.id)
        pkt = EvidencePacket(
            theme=theme.slug, experiment_id=v2.experiment_id,
            simulation_clock=NOW, symbols=["AAA"],
            claims=[PacketClaim(assertion_id=11, document_id=1,
                                content_hash="h", symbol="AAA", quote="q",
                                claim="c")],
            metrics=[ComputedMetric(assertion_id=12, symbol="AAA",
                                    name="exposure_score", value=None,
                                    function_name="f", code_version="v")],
            quarantined_metrics=[ComputedMetric(
                assertion_id=13, symbol="AAA", name="momentum_12_1",
                value=None, function_name="f", code_version="v",
                quarantined_from_analyst=True)],
            macro=[MacroPoint(assertion_id=14, series_id="DGS10",
                              observation_date=NOW.date(), value=1.0)],
        )
        run_slice.write_packet_manifest(conn, pkt, v1, v2, NOW, commit=False)
        value = deepdive._assertion_value(
            conn, "evidence_packet_manifest", v2.id)
        assert value["claim_assertion_ids"] == [11]
        assert value["metric_assertion_ids"] == [12, 13]
        assert value["macro_assertion_ids"] == [14]
        assert value["thesis_v1_id"] == v1.id
        raise psycopg.Rollback
