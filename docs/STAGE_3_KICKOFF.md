# STAGE 3 KICKOFF — Forward Paper Workflow

*Work order for Claude Code. Re-read `CLAUDE.md` and `docs/trading-desk-spec-v5.md` first. Stage 2 is complete and human-reviewed at Gate B; this connects the two halves. Repo root: `/home/jared/projects/Agents_TradingDesk`. Tooling: **uv**. Time-box: 12 weeks minimum — **this stage IS the experiment.** It ends at **Gate C** (operational integrity) and then **Gate D** (research value — the go/no-go). Unlike prior stages, most of the 12 weeks is the desk *running*, not code being written.*

---

## Goal of this stage (one sentence)

Wire Stage 1's research roles to Stage 2's deterministic engine through a **CLI human-approval gate**, run the **weekly proposal cycle** forward on a live cadence with milestone monitoring and post-mortems, and let it operate largely unattended for 8–12+ weeks so that at the end you can answer Gate D: **is the desk well-calibrated, and does it beat the controls?**

Almost all deterministic machinery already exists and is tested. Stage 3 is **orchestration, the approval surface, and time** — not new engine code.

---

## What Stages 0–2 give you (this stage is mostly wiring these together)

**Research half (Stage 1):** the four LLM roles (Evidence Collector with the blinding boundary, Research Analyst, Independent Reviewer, Report Assembler), producing a versioned `Thesis` with `Forecast`s and a decision packet mirrored to `~/obsidian-brain/TradingDesk/packets/`. Currently terminates at a research verdict (DRAFT→REJECTED/approved-to-research), **not** at a trade.

**Engine half (Stage 2, `analytics/engine/`):** all pure, all tested, bit-identical replay proven.
- `sizing.py` — `size_basket()` (→ proposed quantities), `enforce_caps()` (§8 hard caps), `identify_clusters()`, `inverse_vol_weights()`.
- `simulate()` (`loop.py:169`) — the pure forward step; consumes `SimulationInput` containing **`ApprovedOrder`s** (`state.py:73`) + `ResolutionEvent`s, produces a `SimulationResult`.
- `execution.py` — `fill_price()`, `slippage_bps()`, `orders_due()`, `due_session()` (next valid session open).
- `ledger.py` — `apply_fill()`, FIFO lots, `flag_wash_sale_candidates()`.
- `portfolio.py` — `mark_to_market()`, `apply_corporate_actions()`.
- `quarantine.py` — `detect()`, `freeze()`, `apply_resolution()`, `timeouts_due()` (the state machine).
- `controls.py` — `step_benchmark()` for the four `BenchmarkNav` series.
- `breakers.py` — `evaluate()` (the four breakers).
- `metrics.py` — `brier_score()`, `mean_brier()`, `calibration_curve()`, `forecast_resolution_effect()` (invariant-7 authority), `sharpe`, `calmar`, `max_drawdown`, `attribution_by_tag()`, `probabilistic_sharpe()`, `block_bootstrap_ci()`.
- `persist.py` — `ReplayManifest`, `persist_run()`, `result_hash()`, `reconstruct_result()`, `DeterminismBreach`.

**The seam:** nothing currently turns a research packet into a **proposed** `Order`, presents it for **human approval**, or feeds the resulting `ApprovedOrder`s into `simulate()` on a recurring cadence. That seam is Stage 3.

**Known invariant-6 exception (do not re-litigate):** `FORCED_LIQUIDATION` orders (quarantine timeout) are the one un-approved order path; the compensating control is `contains_forced_resolution` taint-forward. Stage 3's job is to **surface these visibly in reporting**, not to change the mechanism.

---

## Build order (wiring, then cadence, then let it run)

### 1. Proposal builder (`agents/` → engine handoff)
A deterministic module that takes an **analyst-approved, reviewer-passed** thesis + basket and calls `size_basket()` / `enforce_caps()` to produce **proposed** `Order` rows (`status=PROPOSED`, `proposed_qty` set, `approved_qty` NULL, `decision_packet_doc_id` pointing at the evidence packet). This is Python turning research into a sized proposal — no LLM produces a quantity (Prime Directive). Sizing runs at proposal time, upstream of the replay boundary (per Stage 2's determinism architecture).

### 2. CLI approval gate (`ops/approve.py` or similar — the human surface)
The invariant-6 gate, as a CLI (React queue deferred to Stage 4+ — the spec's anti-scope rule; build the honest minimum first). For each `PROPOSED` order it:
- Renders the decision packet (thesis, causal chain, exposure basket **with provenance**, forecasts, valuation check, all four exit criteria, reviewer's challenges + do-nothing case) — reads the Obsidian packet + DB.
- Lets the human **approve / modify qty / reject** per order, capturing `approved_qty` and `human_rationale` (logged separately from the proposal so agent vs. human-overlay performance can be separated — invariant 6).
- Writes approved orders as `ApprovedOrder` inputs for the engine; rejections are logged (and, where they kill a thesis, routed to `research_genealogy`).
- Shows any `contains_forced_resolution` / open `DataIncident` state so the human sees quarantine activity.

**No order reaches the engine without passing through here** (except the documented `FORCED_LIQUIDATION` path).

### 3. Weekly cycle orchestrator (`ops/weekly_cycle.py`, systemd timer)
The recurring loop, idempotent (safe to run twice — per CLAUDE.md):
- **Research pass:** run the four roles over the active theme(s) → new/updated theses + packets.
- **Proposal pass:** proposal builder → `PROPOSED` orders.
- **Halt for approval:** the cycle **stops and notifies**; it does not proceed to fills until the human has run the approval CLI. (This is a real human-in-the-loop interrupt, not a timeout-auto-approve.)
- **Engine pass:** feed `ApprovedOrder`s + any `ResolutionEvent`s into `simulate()`; persist the run via `persist_run()` with its `ReplayManifest`.
- **Report pass:** render the weekly report (below).
Cadence: weekly proposal + **event-driven exceptions** (an exit trigger or milestone firing can run a cycle off-schedule).

### 4. Daily monitoring service (`ops/daily_monitor.py`, systemd timer)
Between weekly cycles, runs each session:
- Ingest new prices/filings/FRED (existing adapters), respecting eligibility.
- Step the engine forward one session (MTM, fills due, corporate actions, quarantine ticks, breaker evaluation, controls).
- **Milestone monitoring:** check each active thesis's `Milestone`s against newly-eligible evidence; a **falsified milestone → `CAUSAL_BREAK`** (liquidate basket, resolve forecasts); a confirmed one updates status. Milestone checks may use the LLM to *read* whether an event occurred, but the **resolution effect is deterministic** (`forecast_resolution_effect`).
- Raise alerts (see §6) for anything needing the human: approval pending, breaker tripped, quarantine incident, milestone break, pipeline failure.

### 5. Post-mortems & lessons (`agents/postmortem.py`)
On any position/thesis close, generate a structured retro scoring **thesis correctness and trade correctness separately** (causal accuracy, milestone accuracy, exposure mapping, valuation judgment, entry timing, sizing, **exit discipline — was the right `ExitType` used?**, execution, human-override effect). Write to the `lessons` table + Obsidian. Hypothesis generation retrieves relevant lessons before proposing new theses (the learning loop).

### 6. Reporting & alerts (extends Stage 1's renderer)
- **Daily report** (→ briefing pipeline + Obsidian): portfolio snapshot (positions, weights, P&L **pre/post illustrative-tax** vs **all four controls**), risk-flag/breaker state, milestone/exit events, any `contains_forced_resolution` activity, pipeline health, "what the desk is thinking."
- **Weekly report:** new/updated theses with decomposition + reviewer summary; proposed rebalance **awaiting approval**; per-`strategy_tag` attribution; **calibration snapshot** (running Brier + calibration curve); lessons added; agent decision audit trail.
- **Alerts:** a simple channel (ntfy or Telegram — pick one, record it) for approval-pending, breaker trips, milestone breaks, quarantine incidents, pipeline failures. This is what lets you "just check in."

### 7. Slippage calibration
Now that real opens are accumulating, compare modeled fills vs. observed subsequent opens/quotes and tune the `config/` slippage tiers. Recalibration is a config change (versioned), not a code change; note it in `DECISIONS.md`.

---

## Explicitly NOT in Stage 3 (raise if tempted)

- ❌ **React / web approval queue or dashboard** — CLI first (anti-scope; the React queue is the *first* Stage 4+ trigger, built once you've felt weekly approval by hand).
- ❌ **LangGraph / Langfuse** — plain Python orchestration until it genuinely hurts.
- ❌ **Broker / live orders / real money** — the broker paper account is a *pre-live* gate (Stage 4); the simulator remains the execution venue.
- ❌ **Shorts / options / futures** — separate future projects.
- ❌ **Expanding the universe to 500 names** — the theme-basket universe suffices for the experiment; the Tiingo paid-tier scaling cost is a Stage 4 decision. (If you widen at all, only as far as the active themes' baskets require.)
- ❌ New deterministic engine machinery — if you're writing new ledger/fill/risk math, stop: that was Stage 2, and changing it breaks replay.

---

## Gate C — Operational Integrity (must hold before trusting the run)

- [ ] Restart/recovery tested; the weekly cycle and daily monitor are idempotent (safe to run twice; a duplicate run changes nothing).
- [ ] Late/failed data never generates orders (data-integrity breaker halts the cycle; tested).
- [ ] The human-approval interrupt genuinely blocks: no path (save documented `FORCED_LIQUIDATION`) fills an order that wasn't approved via the CLI; tested.
- [ ] Milestone → `CAUSAL_BREAK` path works end to end on a real (or fixtured) falsified milestone; forecasts resolve correctly, basket liquidates.
- [ ] Backups (deferred auth from Gate B): **close the rclone `gdrive` + `gh auth` open items now** — Stage 3 accumulates months of NAV/research worth protecting — OR record an explicit renewed deferral with the human's sign-off. A restore test passes against whatever offsite exists.
- [ ] Survived unattended: model unavailability, network outage, source schema change, disk pressure, timezone edge — without silent failure (loud failure + alert is acceptable).
- [ ] **8–12 consecutive weeks of the full cycle with no silent failures.** (This is mostly elapsed time, not code.)

## Gate D — Research Value (the go/no-go; evaluate at end of the run)

- [ ] Calibration curves are meaningful and improving; running **mean Brier beats a naive base-rate forecaster.**
- [ ] The desk **beats or matches `same_basket_no_llm_timing`** — else the LLM timing/sizing overlay adds nothing over just holding the basket.
- [ ] The desk **beats `momentum_top10`**, or the honest conclusion is drawn and written down.
- [ ] **Abstention quality** is demonstrable (the desk's no-trade decisions were, on balance, correct).
- [ ] A written go/no-go: proceed toward the Stage 4 pre-live gate, or keep the desk as a calibrated research/reporting assistant with rules-based execution. **Both are valid outcomes** — record which, and why, against the pre-declared EXP-001 criteria.

When Gate C holds and the run has produced enough resolved forecasts and closed trades to evaluate Gate D honestly, **stop and summarize for the human.** Stage 4 (pre-live: broker paper integration gate, real-capital planning, human-approval-per-live-order, the React queue) is a separate decision made only if Gate D says proceed.

---

## Working reminders

- Stage 3 is wiring + cadence + time. If you're writing new deterministic math, you've drifted into Stage 2's territory — stop and raise it.
- The approval interrupt is real: the cycle **waits** for the human; it never auto-approves on a timer.
- LLMs may *read* whether a milestone occurred; the *resolution effect* is always deterministic Python.
- Every timer job idempotent; every failure loud (alert), never silent.
- Keep proposal-time sizing upstream of the replay boundary so determinism holds.
- Honor Stage 0–2 schema/engine names exactly; extend deliberately and log it.
- The point of the 12 weeks is to *run*, not to keep building. Resist the urge to add features mid-experiment — feature-freeze once the cycle is stable, so Gate D measures the desk, not a moving target.
- Anything that breaks an invariant or touches the anti-scope list: raise it before acting.

---

## After Gate D (context, not this stage's work)

If Gate D says proceed, Stage 4 is the **pre-live gate**: broker paper account integration (order-lifecycle realism — rejections, partial fills, session rules), real-capital planning with per-live-order human approval, the React approval queue (now that weekly approval has been felt by hand), optimal backup architecture revisited, and secrets hardened (sops/age). No real dollar moves before that gate. If Gate D says the desk is a research assistant not an alpha source, that is a genuine success of the experiment — the calibration and abstention machinery is valuable on its own, and the honest null result is the thing most trading projects never bother to measure.
