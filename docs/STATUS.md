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

- **Stage:** 0 — Define the Experiment
- **Gate:** approaching A (Gate A is the Stage 1 exit; Stage 0 has its own exit checklist below)
- **Done:** (in progress — updated as deliverables land)
- **Blocked:** nothing

## Stage 0 exit check

- [ ] Repo skeleton exists; tooling chosen and locked; env actuals recorded in `DECISIONS.md`.
- [ ] All §3 tables exist as Pydantic + SQL, with provenance types, eligibility columns, exit-type enums, and the NAV taint flag structurally present.
- [ ] Schema round-trip + invariant-structure tests pass.
- [ ] Data-source contracts written for EDGAR, FRED, and the price source.
- [ ] `EXPERIMENT_REGISTRY.md` freezes the experiment with pre-declared Gate D criteria, including the accepted null result.
- [ ] One starter theme + two candidates drafted to the §5 template.
- [ ] `STATUS.md` reflects all of the above.
