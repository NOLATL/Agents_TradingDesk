# STAGE 1 KICKOFF — Evidence Vertical Slice

*Work order for Claude Code. Re-read `CLAUDE.md` and `docs/trading-desk-spec-v5.md` first. Stage 0 is complete and human-reviewed; this builds on it. Repo root: `/home/jared/projects/Agents_TradingDesk`. Tooling: **uv** (`uv add` / `uv run`). Time-box: 6 weeks. This stage ends at **Gate A**, then stops for human review — it does NOT roll into Stage 2 (the simulator).*

---

## Goal of this stage (one sentence)

Take **one theme through five companies** end to end — ingest real source data with correct eligibility timestamps and provenance, run the four LLM roles over it under the blinding rule, and emit a single **markdown decision packet** where every assertion traces to a citation or a named function — with **no simulator, no orders, no sizing, no dashboard.**

This is a *vertical slice*: thin but complete, proving the data→evidence→thesis→review→report path works honestly before any portfolio machinery exists in Stage 2.

---

## What Stage 0 gives you (build on these, don't recreate)

- Schemas in `schemas/`: `models.py` (18 `DeskModel` classes incl. `Document`, `Extraction`, `Assertion`, `Thesis`, `Forecast`, `Milestone`, `AgentRun`, `DataIncident`, `ResearchGenealogy`, …), `enums.py` (`ProvenanceType`, `ExitType`, `ForecastOutcome`, `ResolutionCause`, `AgentRole`, `IncidentClass`, …), `ddl.sql`. **Use these exact classes and enums. If a field is genuinely missing, add it to the schema deliberately and note it in `DECISIONS.md` — do not invent a parallel model.**
- Data contracts in `contracts/`: `edgar.yaml`, `fred.yaml`, `stooq.yaml`. These define eligibility, timezone, revision, and license rules. **The adapters must honor them, not re-decide them.**
- `EXPERIMENT_REGISTRY.md` (EXP-001) with pinned model tags and Gate D criteria.
- Starter theme in `docs/themes/` (grid constraint) + two candidates. Use **grid constraint** as the slice's theme.
- Compose Postgres (`compose.scratch.yaml`) — now becomes the persistent Stage 1 dev database. Confirm whether to promote it from "scratch" naming to a stable `compose.yaml`; record the choice in `DECISIONS.md`.

---

## Build order (layers bottom-up; each testable before the next)

### 1. Persistence layer
Bring up the Compose Postgres as the stable dev DB, apply `schemas/ddl.sql`, and add a thin repository module (`data/repository.py` or similar) that reads/writes the Stage 0 Pydantic models via psycopg — no ORM (Stage 0 deliberately avoided SQLAlchemy; keep it that way). Every insert path must enforce: provenance type non-null, eligibility timestamp present. Migrations can be plain ordered `.sql` files for now.

### 2. Ingestion adapters (deterministic Python, honor the contracts)
One adapter per source, each writing raw responses immutably to `documents` (hashed, per invariant 4) and deriving `first_eligible_decision_time` from the contract's rules:
- **Stooq** — daily OHLCV for the five-company slice universe. CSV endpoint, no key.
- **FRED** — the macro series the grid-constraint theme needs (record which in the theme doc); use ALFRED vintages where the contract says revisions matter.
- **SEC EDGAR** — filings/transcripts for the five companies (full-text search + document fetch); eligibility from acceptance timestamp → market TZ → next valid session.

Requirements:
- **Idempotent**: re-running an adapter re-fetches nothing already cached by hash and inserts no duplicate rows.
- **`exchange_calendars`** for every session-date derivation. Never assume Mon–Fri.
- **Data reconciliation stub**: where two sources overlap (none critical this slice, but wire the pattern), classify discrepancies rather than silently picking one.
- Raw bytes → `documents`; extracted text → `extractions`; both timestamped.

### 3. Deterministic analytics (pure functions, unit-tested)
Only what the slice's decision packet needs — no portfolio math yet:
- **Exposure metrics**: given a company's filings/fundamentals, compute the measured exposure figures the analyst is allowed to use (revenue %, segment profit, backlog, capex, management-emphasis counts). These are `COMPUTED` assertions.
- **Valuation snapshot**: basic current-multiple computation from fundamentals (P/E, EV/EBITDA, etc. as data allows) — for the valuation-check, computed not model-guessed.
- **Momentum/volatility**: compute and **store** these, but they are **quarantined from the analyst** (invariant 5). They exist for the eventual controls and for the reviewer's "is price driving the narrative?" check — never as a basket-construction input.

Pure functions, deterministic, no network, tested against known inputs with Hypothesis where sensible.

### 4. Local inference wrapper
A thin Ollama client (OpenAI-compatible endpoint) that:
- Pins the model tag from `EXPERIMENT_REGISTRY.md` and **logs tag/hash + prompt version to `agent_runs` on every call** (invariant via CLAUDE.md).
- Raises Ollama context above 32K for filing-length inputs; chunk + retrieve for very long 10-Ks rather than truncating silently.
- Wraps tool/JSON calls with **parse-and-retry** (Ollama leaks tool calls into `content` on some models).
- Enforces strict output schemas; a malformed/ungrounded output is rejected, not massaged.

### 5. The four LLM roles (plain Python + Pydantic — NOT LangGraph)
Implement as ordinary functions/classes passing typed state. Each writes schema-validated objects; none computes a number that gets acted on.
- **Evidence Collector** — retrieves via the adapters, caches immutably, extracts claims as `SOURCE_FACT` assertions **with citation spans + timestamps**. Enforces the **blinding boundary**: it is the component that must *never* place price-performance data into the analyst's context. Make this boundary explicit and testable.
- **Research Analyst** — builds a `Thesis` for grid constraint over the five companies: causal chain, ranked exposure basket (justified **only** by the `COMPUTED` exposure metrics + `SOURCE_FACT`s, never price), valuation check, 6–10 `Forecast`s with resolution criteria + horizons, and all four `ExitType` criteria. Emits `JUDGMENT` assertions clearly typed.
- **Independent Reviewer** — sees only the evidence packet + analyst output (not the analyst's reasoning trace). Hunts disconfirming primary evidence, challenges exposure mapping and causal timing, checks whether price action is driving the narrative, and writes an explicit **do-nothing case**. Log pre- and post-review thesis versions.
- **Report Assembler** — renders the validated objects into the markdown decision packet. Adds no new claims.

### 6. The decision packet (the stage's tangible output)
One markdown file per run, written to `reporting/output/` and mirrored into the Obsidian vault at `/home/jared/obsidian-brain/` (confirm the exact subfolder with the human; the vault has an `Economics/` tree — likely a sibling `TradingDesk/` folder). It contains: the thesis, the causal chain, the ranked basket **with every exposure number citation-or-function traced**, the forecasts, the valuation check, the four exit criteria, the reviewer's challenges and do-nothing case, and a provenance appendix where **every assertion resolves to a citation or a named function+version**. No sizing, no orders.

---

## Explicitly NOT in Stage 1 (raise if tempted)

- ❌ Simulator, ledger, fills, NAV, sizing, risk limits, breakers — **all Stage 2**.
- ❌ Orders or an approval flow — no orders exist yet.
- ❌ Control benchmarks running — they need the simulator (Stage 2).
- ❌ LangGraph, Langfuse, web dashboard — earned later.
- ❌ Shorts/options/futures, broker anything.
- ❌ The other two themes — one theme, five companies. Depth over breadth for the slice.

If a task pulls toward any of these, it's the wrong task for this stage.

---

## Gate A — Data & Research Integrity (Stage 1 exit; all must hold)

- [ ] Every assertion in a decision packet traces to a source citation or a named Python function+version.
- [ ] Point-in-time eligibility enforced; a test proves no packet consumed a record whose `first_eligible_decision_time` was ahead of the run's simulation clock.
- [ ] **Basket construction demonstrably blinded to price history** — a test shows the analyst's context contains no price-performance data, and removing price data does not change basket output.
- [ ] Raw research responses cached immutably to `documents`; extractions reference stored hashes; re-runs produce no duplicate fetches or rows (idempotency test).
- [ ] Vendor/source discrepancies are *classified* (the reconciliation pattern exists), not silently resolved.
- [ ] Thesis changes versioned; the reviewer's pre/post versions both persisted; any rejected hypothesis logged to `ResearchGenealogy`.
- [ ] Forecasts recorded with resolution criteria, horizons, and exit-type-aware resolution semantics.
- [ ] `agent_runs` logs model tag/hash + prompt version for every LLM call.
- [ ] The grid-constraint slice produces a complete, human-readable decision packet in Obsidian.
- [ ] `STATUS.md` and `DECISIONS.md` updated; env re-verifications done (ufw at first reachable service — none if Postgres stays localhost; R2/restic once the DB holds data worth backing up).

When every box holds, **stop and summarize for the human.** Do not begin Stage 2.

---

## Working reminders

- Bottom-up: persistence → ingestion → analytics → inference → roles → packet. Each layer tested before the next.
- Analytics are pure and deterministic; LLMs never produce acted-on numbers.
- Small scoped commits; the human reads the history.
- Honor the contracts and the Stage 0 schema names exactly; extend deliberately and log it, never fork a parallel model.
- The blinding boundary (invariant 5) is the subtlest thing here — make it explicit code with its own test, not an implicit convention.
- Anything that breaks an invariant or touches the anti-scope list: raise it before acting.
