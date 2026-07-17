"""Stage 1 vertical-slice orchestration: collector → analyst → reviewer →

reporter → decision packet. Plain Python; the simulation clock is fixed at
start and every source read goes through the eligibility gate.

    uv run python -m agents.run_slice
"""

from datetime import datetime, timezone

from agents.roles import analyst, evidence, reporter, reviewer
from data import repository as repo
from data.db import connect
from reporting import packet as packet_renderer
from schemas.models import Experiment


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


def run() -> list:
    clock = datetime.now(timezone.utc)
    with connect() as conn:
        experiment = ensure_experiment(conn)
        print(f"[1/4] evidence collection (clock {clock.isoformat()}) …")
        pkt = evidence.collect(conn, experiment.id, clock)
        print(f"      {len(pkt.claims)} claims, {len(pkt.metrics)} metrics,"
              f" {len(pkt.quarantined_metrics)} quarantined, {len(pkt.macro)} macro points")
        print("[2/4] analyst …")
        thesis_v1, analyst_out = analyst.construct_thesis(conn, pkt, clock)
        print(f"      thesis v1 id={thesis_v1.id}, {len(analyst_out.forecasts)} forecasts")
        print("[3/4] reviewer …")
        thesis_v2, review_out = reviewer.review(conn, pkt, thesis_v1, analyst_out, clock)
        print(f"      verdict {review_out.verdict} → thesis v2 id={thesis_v2.id}")
        print("[4/4] reporter …")
        summary = reporter.summarize(conn, pkt, thesis_v2, analyst_out, review_out, clock)
        content = packet_renderer.render(
            conn, pkt, thesis_v2, analyst_out, review_out, summary, clock
        )
        paths = packet_renderer.write(content, clock)
        for p in paths:
            print(f"      wrote {p}")
        return paths


if __name__ == "__main__":
    run()
