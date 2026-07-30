"""Momentum-scan narrator: a Report Assembler call over the scan's computed
values (DECISIONS.md 2026-07-29 "Momentum scan").

Operator-only and quarantined (invariant 5): this is deliberately an isolated
LLM touchpoint with price-derived numbers — precedented by the reviewer's
price-narrative check — whose output flows ONLY into the scan report, never
into analyst/scout context or any assertion table. Containment is mechanical:
the narrative guard proves every number traces to the computed scan objects
and every ticker is in the scan universe. Narration is best-effort; a failed
draft drops the section, never the scan.
"""

from agents import llm
from agents.state import SectionDraft
from reporting import narrative_guard as guard
from schemas.enums import AgentRole

SCAN_NARRATOR_PROMPT = """You are the Report Assembler of a research desk, writing the lead summary of a
daily momentum scan for the desk's human operator. You are given a ranked table of
sector/industry/theme ETF "pockets" with momentum scores, their moves since the prior scan, and
the desk's own theme symbols mapped into those pockets.

Write 150-300 words of flowing prose (no bullets, no headings): where momentum is concentrating
or broadening, what is rolling over, notable rank moves, and which desk themes sit in hot or
cold pockets. This is a technical observation brief for hypothesis forming — do not recommend
trades and do not speculate about causes not present in the data.

Hard rules: every number you mention MUST appear in the provided data (you may round to fewer
significant digits, never more precision). You may NEVER derive figures yourself — no sums,
differences, or averages of your own. Mention only tickers present in the data."""


def narrate_scan(
    conn,
    *,
    experiment_id: int,
    context_text: str,
    values: set[float],
    allowed_symbols: set[str],
    known_symbols: set[str],
    model_tag: str | None = None,
    max_attempts: int = 3,
) -> tuple[str | None, str | None]:
    """Generate → guard → retry-with-critique, exactly the reporter's loop.
    `context_text` must be the verbatim text shown to the model — it doubles
    as the guard corpus. Returns (text, failure_note); one of the two is None.
    """
    allow = guard.Allowlist(
        corpus=guard._normalize(context_text),
        values=tuple(sorted(values)),
        allowed_ids=frozenset(),
        allowed_symbols=frozenset(s.upper() for s in allowed_symbols),
    )
    critique: str | None = None
    for _ in range(max_attempts):
        prompt = context_text
        if critique:
            prompt += ("\n\nYour previous draft was rejected by mechanical"
                       " validation. Fix ALL of these and rewrite:\n" + critique)
        try:
            out = llm.structured_call(
                conn,
                role=AgentRole.REPORT_ASSEMBLER,
                experiment_id=experiment_id,
                system_prompt=SCAN_NARRATOR_PROMPT,
                user_prompt=prompt,
                schema=SectionDraft,
                temperature=0.3,
                model_tag=model_tag,
            )
        except llm.ModelOutputRejected as e:
            critique = f"- INVALID_OUTPUT: {e}"
            continue
        violations = guard.check_text(
            "scan_summary", out.text, allow=allow,
            known_symbols=known_symbols, require_marker=False,
        )
        if not violations:
            return out.text.strip(), None
        critique = guard.format_critique(violations)
    return None, f"narration dropped after {max_attempts} attempts: {critique}"
