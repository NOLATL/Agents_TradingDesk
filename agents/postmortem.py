"""Post-mortems (§9): on thesis close, score thesis correctness and trade

correctness SEPARATELY. Deterministic facts assembled by Python; the LLM
writes clearly-typed JUDGMENT lessons per dimension; rows land in `lessons`
+ the vault. The hypothesis pass reads lessons before proposing (learning
loop). Read-only over the ledger — safe to run mid-experiment.

    uv run python -m agents.postmortem
"""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agents import llm
from config.loader import CONFIG, SLICE
from data import repository as repo
from data.db import connect
from schemas.enums import AgentRole
from schemas.models import Experiment, Forecast, Lesson, Thesis

DIMENSIONS = [
    "causal_accuracy", "milestone_accuracy", "exposure_mapping",
    "valuation_judgment", "entry_timing", "sizing", "exit_discipline",
    "execution", "human_override",
]

SYSTEM_PROMPT = """You write post-mortem lessons for a research desk. You receive the FACTS of a
closed thesis (forecasts + outcomes, trades, exits, human overrides). For each dimension you are
given, write one honest lesson grounded ONLY in those facts. Score thesis correctness (was the
reasoning right?) separately from trade correctness (was the execution right?) — a right thesis
can be a bad trade and vice versa. No hindsight moralizing; be specific."""


class LessonDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    lesson: str = Field(description="one specific, factual lesson")


class PostmortemOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lessons: list[LessonDraft] = Field(min_length=3, max_length=9)


def facts_for(conn, thesis: Thesis) -> str:
    forecasts = repo.fetch(conn, Forecast, "thesis_id = %s ORDER BY id", (thesis.id,))
    forecast_lines = "\n".join(
        f"- P={f.probability:.2f} '{f.statement[:100]}' → {f.outcome.value if f.outcome else 'unresolved'}"
        f" ({f.resolution_cause.value if f.resolution_cause else '-'})"
        for f in forecasts
    )
    trades = conn.execute(
        "SELECT t.symbol, t.side, t.qty, t.fill_price, t.exit_type, t.executed_at::date"
        " FROM trades t JOIN orders o ON t.order_id = o.id WHERE o.thesis_id = %s"
        " ORDER BY t.id", (thesis.id,),
    ).fetchall()
    trade_lines = "\n".join(
        f"- {s} {side} {q} @ {p} exit={e or '-'} on {d}" for s, side, q, p, e, d in trades
    ) or "- no trades (abstention)"
    overrides = conn.execute(
        "SELECT human_rationale FROM orders WHERE thesis_id = %s"
        " AND human_rationale IS NOT NULL", (thesis.id,),
    ).fetchall()
    override_lines = "\n".join(f"- {r[0]}" for r in overrides) or "- none"
    return (
        f"THESIS (v{thesis.version}, final status {thesis.status.value}): {thesis.title}\n"
        f"CAUSAL CHAIN: {thesis.causal_chain[:400]}\n\n"
        f"FORECASTS:\n{forecast_lines}\n\nTRADES:\n{trade_lines}\n\n"
        f"HUMAN OVERRIDES:\n{override_lines}"
    )


def run_postmortem(conn, thesis: Thesis) -> list[Lesson]:
    existing = conn.execute(
        "SELECT count(*) FROM lessons WHERE thesis_id = %s", (thesis.id,)
    ).fetchone()[0]
    if existing:
        return []  # idempotent
    output = llm.structured_call(
        conn,
        role=AgentRole.REPORT_ASSEMBLER,
        experiment_id=thesis.experiment_id,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=facts_for(conn, thesis)
        + f"\n\nDimensions to score: {', '.join(DIMENSIONS)}",
        schema=PostmortemOutput,
    )
    now = datetime.now(timezone.utc)
    lessons = []
    for draft in output.lessons:
        if draft.dimension not in DIMENSIONS:
            continue
        lesson = Lesson(
            thesis_id=thesis.id, dimension=draft.dimension,
            lesson_text=draft.lesson, strategy_tag=thesis.strategy_tag,
            created_at=now,
        )
        repo.insert(conn, lesson)
        lessons.append(lesson)
    conn.commit()

    vault = Path(SLICE["vault_dir"]) / "postmortems"
    vault.mkdir(parents=True, exist_ok=True)
    body = [f"# Post-mortem — {thesis.title} (v{thesis.version}, {thesis.status.value})", ""]
    body += [f"- **{l.dimension}**: {l.lesson_text}" for l in lessons]
    (vault / f"thesis-{thesis.id}.md").write_text("\n".join(body) + "\n")
    return lessons


def run_all() -> int:
    with connect() as conn:
        exp = repo.fetch_one(
            conn, Experiment, "name = %s", (CONFIG["stage3"]["live_experiment"],)
        )
        closed = repo.fetch(
            conn, Thesis,
            "experiment_id = %s AND status IN ('INVALIDATED', 'CLOSED') ORDER BY id",
            (exp.id,),
        )
        n = 0
        for thesis in closed:
            n += len(run_postmortem(conn, thesis))
        return n


if __name__ == "__main__":
    print(f"lessons written: {run_all()}")
