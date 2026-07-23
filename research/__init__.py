"""Research module — the absorbed economics desk (consolidation 2026-07-22).

Nightly functions on the desk's own schedulers:
- candidate_scout: one unified pass proposes exactly ONE mandatory candidate
  theme per session into the intake queue (the human accepts at most one per
  day across ALL sources — ops/approve.py). Zero candidates is a failure
  state that notifies, not a quiet outcome.
- theme_brief: narrative vault brief per theme with an ACTIVE thesis.
- review_gate: Qwen generates, claude-sonnet-5 reviews. Approved work flows
  to the vault/briefing; rejected work is regenerated with the critique;
  final failures are surfaced loudly, never silently dropped.
"""
