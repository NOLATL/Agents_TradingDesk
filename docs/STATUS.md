# STATUS

## Constitution check (Stage 0, in my own words)

**Prime Directive:** No number an LLM produces is ever acted on. Prices, weights, sizes, fills, risk checks, P&L, metrics — all come from deterministic, tested Python. The LLMs' entire job is language: extract evidence, build theses, audit theses, write reports. Wrapping a model's number in Pydantic makes it well-formatted, not true — validation is not verification. If a design ever routes a model quantity into a calculation or an order, that design is wrong and gets rebuilt so Python produces the quantity.

**The eight invariants, restated:**

1. **Provenance everywhere.** Every stored fact is typed at birth — extracted-with-citation (`SOURCE_FACT`), computed-by-named-function (`COMPUTED`), model-opinion-never-an-input (`JUDGMENT`), or human-decision-with-rationale (`HUMAN`). An untyped assertion cannot enter the database; the schema refuses it.
2. **No reading the future.** Every source record derives a `first_eligible_decision_time`; nothing may consume a record whose eligibility time is ahead of the simulation clock. This is absolute — it is the anti-look-ahead guarantee the whole experiment stands on.
3. **The ledger replays exactly.** Same snapshot + config + code + approved orders ⇒ bit-identical ledger and NAV, proven by a formal test (Gate B). Scope matters: *ledger* replay is deterministic; *research* replays from stored artifacts (packets, evidence, model/prompt hashes), never by re-running a model.
4. **Keep what you cite.** Every research response is serialized to `documents` on first fetch, hashed, and never mutated. Extractions point at the hash. If we didn't keep it, we can't cite it.
5. **The analyst never sees price history.** Baskets are ranked only by measured exposure (revenue %, backlog, segment profit, capex, management emphasis) from filings/transcripts. Price performance is barred at the evidence-collector boundary — otherwise "analysis" is laundered hindsight and the momentum control silently collapses.
6. **A human approves every order.** Including paper orders. The approval shows the evidence packet; original proposal and human modification are logged separately so their contributions can be measured apart.
7. **Trade exits don't touch forecast scores.** `PRICE_STOP` and `VALUATION_EXIT` close trades while forecasts keep scoring; only `CAUSAL_BREAK` or horizon resolution resolves forecasts. Market noise must never enter the Brier score.
8. **Risk and quarantine are code the LLM can't touch.** Limits and the corporate-action quarantine are deterministic rules. Unsupported corporate actions freeze-and-mark and alert a human; nothing is silently computed, and forced resolutions taint the NAV series visibly.

## Current state

- **Stage:** 0 — Define the Experiment — **COMPLETE, awaiting human review**
- **Gate:** approaching A (Gate A is the Stage 1 exit; Stage 0's own exit checklist is fully checked below)
- **Done:** repo skeleton + uv-locked tooling; all 17 §3 tables as Pydantic + SQL with 74 passing round-trip/invariant tests (validated against scratch Compose Postgres 18.4, applied twice for idempotency, torn down); contracts for EDGAR/FRED/Stooq; EXPERIMENT_REGISTRY.md (EXP-001) with pre-declared Gate D criteria and the accepted null result; starter theme (grid constraint) + two candidates (nuclear renaissance, housing BTR pivot) in `docs/themes/`.
- **Blocked:** nothing. **Stage 1 does not begin until the human reviews Stage 0.**
- **Not built (per work order):** no ingestion, no agents, no simulator, no dashboard, no real rows.

## Stage 0 exit check

- [x] Repo skeleton exists; tooling chosen and locked (uv, `uv.lock`); env actuals recorded in `DECISIONS.md`.
- [x] All §3 tables exist as Pydantic + SQL, with provenance types, eligibility columns, exit-type enums, and the NAV taint flag structurally present.
- [x] Schema round-trip + invariant-structure tests pass (74 passed against scratch Postgres; skipped cleanly when DB absent).
- [x] Data-source contracts written for EDGAR, FRED, and Stooq (`contracts/`).
- [x] `EXPERIMENT_REGISTRY.md` freezes EXP-001 with pre-declared Gate D criteria, including the accepted null result.
- [x] One starter theme + two candidates drafted to the §5 template (`docs/themes/`).
- [x] `STATUS.md` reflects all of the above.
