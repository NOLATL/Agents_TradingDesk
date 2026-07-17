# STAGE 0 KICKOFF — Define the Experiment

*Work order for Claude Code. Read `CLAUDE.md` and `docs/trading-desk-spec-v5.md` fully before starting. Stage 0 is a **schema-and-decision stage with essentially no application code** — time-box one week. The goal is a repo skeleton, the data-model schemas, a frozen set of decisions, one starter theme, and pre-declared success criteria. Do not build ingestion, agents, or the simulator yet; those are Stage 1+.*

---

## Before you write anything

1. Confirm you have read `CLAUDE.md` and can restate the Prime Directive and the eight invariants in your own words in `docs/STATUS.md`. This is a check that the constitution is loaded, not busywork.
2. Verify the environment assumptions in `CLAUDE.md` § Environment against the actual machine. Where reality differs, record the truth in `docs/DECISIONS.md` and proceed with reality, not the assumption. In particular: confirm the Postgres approach (system apt install vs. Docker Compose container), the Obsidian vault path, and the Python version available.
3. **Ask me** about anything unverifiable before proceeding.

---

## Deliverables (all of Stage 0)

### 1. Repo skeleton
Create the structure the spec implies, with empty packages and README stubs — **directories and `__init__.py`/placeholder files only, no implementation:**

```
trading-desk/
  CLAUDE.md
  docs/
    trading-desk-spec-v5.md      # the authoritative spec (I will place it here)
    STATUS.md                    # current stage/gate, updated every session
    DECISIONS.md                 # decision log: tooling, env actuals, rationale
    EXPERIMENT_REGISTRY.md       # the frozen experiment definition (deliverable 4)
  data/                          # (empty) ingestion adapters + reconciliation — Stage 1
  analytics/                     # (empty) pure-function modules — Stage 1
    engine/                      # (empty) simulator — Stage 2
  agents/                        # (empty) the four LLM roles — Stage 1
  schemas/                       # Pydantic + SQL schemas — THIS STAGE
  reporting/                     # (empty) — Stage 1
  config/                        # versioned config — begins this stage
  tests/                         # begins this stage (schema round-trip tests)
  contracts/                     # data-source YAML contracts (deliverable 3)
```

Choose the Python tooling (uv / poetry / venv), initialize it, create a locked dependency file with just what Stage 0 needs (Pydantic, a Postgres driver, a test runner), and record the choice + rationale in `docs/DECISIONS.md`.

### 2. Data-model schemas (the heart of Stage 0)
Implement, as **both Pydantic models and SQL DDL** (kept in `schemas/`), every table in spec §3, with the bitemporal/eligibility columns, provenance types, forecast fields (including `resolution_cause` and the exit-type-aware resolution semantics), and the immutable research cache (§3.8). Cover at minimum:

`documents` (immutable raw + cache fields), `extractions`, `assertions` (provenance), `theses`, `forecasts`, `milestones`, `orders`, `trades`, `tax_lots`, `positions`, `daily_nav`, `benchmarks_nav`, `lessons`, `agent_runs`, `data_incidents`, `experiments`, plus a research-genealogy table for rejected hypotheses (§3.5).

Requirements:
- Encode the **four exit types** and their trade/thesis/forecast effects (spec §5) as enums + documented semantics, so downstream stages cannot conflate them.
- Encode the **provenance type** enum and make the type non-nullable everywhere an assertion is stored.
- Include the `contains_forced_resolution` taint flag on the NAV series (spec §6 quarantine).
- Add schema round-trip tests in `tests/` (Pydantic ↔ SQL, enum integrity, not-null constraints on provenance and eligibility columns). No business logic — just that the schemas are internally consistent and the invariants are structurally enforced where possible.
- Do **not** stand up ingestion or write real rows. If useful, apply the DDL to a scratch database to prove it's valid, then tear it down.

### 3. Data-source contracts
For each source the spec names for Stage 1 (SEC EDGAR, FRED, and the primary EOD price source), write a YAML contract in `contracts/` per spec §3.6: raw/adjusted, currency, timezone, session-date rule, corporate-action treatment, source priority, revision policy, expected publication delay, null rule, and **license/permitted-use**. These are documents, not code — they define how eligibility timestamps will later be derived. Do not implement the adapters.

### 4. Experiment registry (pre-declared success criteria)
Write `docs/EXPERIMENT_REGISTRY.md` freezing this experiment per spec §3.4: universe definition, intended data snapshot approach, model tags/hashes to be pinned, config baseline, cost assumptions, portfolio rules (from the Decision Register §2), benchmark rules (the four forward controls), evaluation window, and — most important — **the pre-declared Gate D success criteria**, written now so they can't be moved later to fit results. Include the honest null result ("well-calibrated but doesn't beat momentum = success").

### 5. Starter theme + two candidates
Draft, as structured markdown following the §5 thesis template (causal chain, 6–10 resolvable forecasts with resolution criteria and horizons, exposure-basket *approach* — not yet the ranked names, since blinded ranking needs Stage 1 evidence tooling — valuation-check plan, all four exit criteria, and a do-nothing case), **one starter theme and two candidates**. Pick themes that are genuinely forward-unresolved to minimize contamination (spec §7). These are the experiment's first inputs; they should be good enough to actually run in Stage 1.

### 6. Update STATUS.md
Record: stage = 0, gate = approaching A, what's done, what's blocked, and the exit check below.

---

## Stage 0 exit check (do not advance to Stage 1 until all true)

- [ ] Repo skeleton exists; tooling chosen and locked; env actuals recorded in `DECISIONS.md`.
- [ ] All §3 tables exist as Pydantic + SQL, with provenance types, eligibility columns, exit-type enums, and the NAV taint flag structurally present.
- [ ] Schema round-trip + invariant-structure tests pass.
- [ ] Data-source contracts written for EDGAR, FRED, and the price source.
- [ ] `EXPERIMENT_REGISTRY.md` freezes the experiment with **pre-declared Gate D criteria**, including the accepted null result.
- [ ] One starter theme + two candidates drafted to the §5 template.
- [ ] `STATUS.md` reflects all of the above.

When every box is checked, **stop and summarize for me** — do not roll into Stage 1. Stage 1 is its own work order and begins only after I review Stage 0.

---

## Reminders specific to this stage

- No ingestion, no agents, no simulator, no dashboard. Schemas and decisions only.
- Prefer the simplest tooling that gives reproducible locked deps.
- If implementing a schema tempts you to add a column "we'll need later," check it against the spec — add it only if the spec calls for it; otherwise note it in `DECISIONS.md` as a deferred idea.
- Keep commits small and scoped; I want to be able to read the history.
- Anything that would break an invariant, or anything on the anti-scope list, gets raised with me before you act.
