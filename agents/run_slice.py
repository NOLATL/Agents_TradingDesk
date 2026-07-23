"""Stage 1 vertical-slice orchestration: collector → analyst → reviewer →

reporter → decision packet. Plain Python; the simulation clock is fixed at
start and every source read goes through the eligibility gate.

    uv run python -m agents.run_slice
"""

from datetime import datetime, timezone

from agents.roles import analyst, evidence, reporter, reviewer
from agents.state import EvidencePacket
from data import repository as repo
from data.db import connect
from reporting import packet as packet_renderer
from schemas.enums import ProvenanceType
from schemas.models import Assertion, Experiment, Thesis


def ensure_experiment(conn) -> Experiment:
    exp = repo.fetch_one(conn, Experiment, "name = 'EXP-001'")
    if exp is None:
        exp = Experiment(
            name="EXP-001",
            registry={"doc": "docs/EXPERIMENT_REGISTRY.md"},
            created_at=datetime.now(timezone.utc),
        )
        repo.insert(conn, exp)
        conn.commit()
    return exp


def write_packet_manifest(
    conn, pkt: EvidencePacket, thesis_v1: Thesis, thesis_v2: Thesis,
    clock: datetime, commit: bool = True,
) -> int:
    """COMPUTED index of this dive's evidence assertion ids, keyed to the
    surviving thesis version — the deep-dive renderer rehydrates from it
    exactly (its fallback is a timestamp/id-window closure)."""
    manifest = Assertion(
        type=ProvenanceType.COMPUTED,
        value={
            "kind": "evidence_packet_manifest",
            "thesis_id": thesis_v2.id,
            "thesis_v1_id": thesis_v1.id,
            "claim_assertion_ids": [c.assertion_id for c in pkt.claims],
            "metric_assertion_ids": [
                m.assertion_id
                for m in pkt.metrics + pkt.quarantined_metrics
            ],
            "macro_assertion_ids": [p.assertion_id for p in pkt.macro],
        },
        function_name="agents.run_slice.packet_manifest",
        code_version=evidence.code_version(),
        created_at=clock,
    )
    repo.insert_assertion(conn, manifest)
    if commit:
        conn.commit()
    return manifest.id


def run(
    symbols: list[str] | None = None, theme: str | None = None,
    theme_id: int | None = None, emphasis_terms: list[str] | None = None,
    fred_series: list[str] | None = None,
):
    """One deep dive. Returns (thesis_v2, packet_paths) — the weekly cycle
    caches the packet against the thesis so the order gate later shows the
    evidence of THIS dive, and the REVIEWED thesis awaits `thesis accept`."""
    clock = datetime.now(timezone.utc)
    with connect() as conn:
        experiment = ensure_experiment(conn)
        print(f"[1/4] evidence collection (clock {clock.isoformat()}) …")
        pkt = evidence.collect(
            conn, experiment.id, clock, symbols=symbols, theme=theme,
            emphasis_terms=emphasis_terms, fred_series=fred_series,
        )
        print(f"      {len(pkt.claims)} claims, {len(pkt.metrics)} metrics,"
              f" {len(pkt.quarantined_metrics)} quarantined, {len(pkt.macro)} macro points")
        print("[2/4] analyst …")
        thesis_v1, analyst_out = analyst.construct_thesis(conn, pkt, clock, theme_id)
        print(f"      thesis v1 id={thesis_v1.id}, {len(analyst_out.forecasts)} forecasts")
        print("[3/4] reviewer …")
        thesis_v2, review_out = reviewer.review(conn, pkt, thesis_v1, analyst_out, clock)
        print(f"      verdict {review_out.verdict} → thesis v2 id={thesis_v2.id}")
        write_packet_manifest(conn, pkt, thesis_v1, thesis_v2, clock)
        print("[4/4] reporter …")
        summary = reporter.summarize(conn, pkt, thesis_v2, analyst_out, review_out, clock)
        content = packet_renderer.render(
            conn, pkt, thesis_v2, analyst_out, review_out, summary, clock
        )
        paths = packet_renderer.write(content, clock, theme=pkt.theme)
        for p in paths:
            print(f"      wrote {p}")
        return thesis_v2, paths


if __name__ == "__main__":
    run()
