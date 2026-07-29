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

- **Stage:** 3 — Forward Paper Workflow — armed at genesis 2026-07-20; **CONSOLIDATED 2026-07-22** into the unified theme pipeline (economics desk absorbed — DECISIONS.md "Consolidation").
- The remaining Gate C work is elapsed time (8–12 clean weeks) + your approvals; Gate D is evaluated at the end of the run against the pre-declared EXP-001 criteria (universe amendment noted in the registry).
- **Timers live:** daily monitor Mon–Fri 23:00 ET (ledger → milestones + daily status rows → report → research intake) → backup 02:30 → weekly deep dives **Fri 23:45 ET** (one per accepted theme). `fleet-economics*` timers retired. Alerts: ntfy topic in `.env`.
- **Your daily job:** the briefing shows the theme queue (one acceptance slot per day, all sources), any REVIEWED theses awaiting your call, one monitoring line per active thesis, and pending orders. Decide with `uv run python -m ops.approve` (`theme accept|reject`, `thesis accept|reject`, `dead`, plus the order gate) — the desk does nothing until you do. CAUSAL_BREAK/TIME_STOP approvals are the confirmation events.
- **Queue at cutover:** 16 CANDIDATE themes (2 genesis candidates + 14 migrated from Agents_Economics, 7 flagged `[in-flight bet]`) trickling through the 1/day accept cap.
- **Scout anti-hallucination overhaul shipped 2026-07-29** (DECISIONS.md): deterministic precheck (Tiingo symbol directory + evidence-ID citations + EDGAR direction labels), RSS summaries in context, conviction field + rebalanced review rubric (honest low-conviction proposals now approvable — you decide at `ops.approve`), `scout_attempts` instrument for the readout. US-only ticker rule (your call). Scout model swap to `qwen3.6:35b` planned ~1 week out (research-layer ruling; config comment in experiment.yaml).
- 310 tests pass; Stage 2 replay suite green, including the new mid-run-activation determinism test.

## Gate C — Operational Integrity (running; code-provable items done)

- [x] Restart/recovery + idempotency: cycle_log claims (COMPLETE→no-op, FAILED→resume from phase) proven live; duplicate live-runner run changes nothing (fixture + live re-run).
- [x] Late/failed data never generates orders: completeness gate + finality guard halt before replay (fixture + live drill 2026-07-17: FAILED loudly at phase `ingest`, alert fired).
- [x] The approval interrupt genuinely blocks: PROPOSED never fills; only CLI-approved orders fill at next open (fixture); FORCED_LIQUIDATION remains the sole documented exception.
- [x] Milestone → CAUSAL_BREAK end to end: falsify → deterministic SELL proposals → approval resolves forecasts (FALSE, cause CAUSAL_BREAK, Brier computed) + basket liquidates; rejection reverts the milestone (fixtures).
- [x] Restatement protection: guard pins restated docs (REVISION incident); prefix breach halts without persisting; bar-pin resolution path in the manifest.
- [~] Backups: local nightly dump + passing restore test; **offsite = renewed human-signed deferral** (DECISIONS.md). Auth commands: `~/.local/bin/rclone config create gdrive drive scope=drive.file` · `~/.local/bin/gh auth login --web`.
- [ ] Survived unattended (model down, network outage, source schema change, disk pressure, tz edges) — accumulates during the run; all failure paths alert loudly by construction.
- [ ] **8–12 consecutive clean weeks** — clock starts at genesis 2026-07-20.

## Gate D — Research Value (evaluate at end of run)

Pre-declared criteria frozen in `docs/EXPERIMENT_REGISTRY.md` (EXP-001): calibration primary (≥60 resolved forecasts, Brier beats base-rate), same-basket + momentum controls, abstention quality, and the accepted null result. The momentum control's YTD run (+52% in the lab smoke) sets an honest, high bar.

## Stage 2 (Gate B) — complete, human-reviewed 2026-07-17
- **Gate:** B (checklist below). **Stage 3 (forward paper workflow) does not begin until the human reviews Stage 2.**
- **Done:** pure-function simulator (`analytics/engine/`): FIFO ledger with wash-sale candidates, adverse-slippage fill model, corporate actions + MTM, quarantine state machine (both branches), four forward controls on identical costs, four breakers, cluster-risk-budgeting sizing with hard §8 caps, calibration metrics with the invariant-7 resolution authority. `price_bars` read-through cache (rebuild-proof), `simulation_runs` manifests, `FORCED_LIQUIDATION` order status (documented invariant-6 exception). Backups: nightly timer live, local dump + restore test passed. 190 tests pass.
- **Headline evidence:** canonical-bytes replay is **bit-identical** — fixed scenarios, Hypothesis-generated scenarios, the full DB fixture path, and 134 real sessions over 7 symbols (result hash `cf77b02e…`). Same-manifest-different-result raises `DeterminismBreach`.

## Open items (human)

1. **Offsite auth (deferred at Gate B close):** run
   `~/.local/bin/rclone config create gdrive drive scope=drive.file` (Drive backups) and
   `~/.local/bin/gh auth login --web --git-protocol https` then tell Claude to push the private repo. Until then backups are local-only (the script fails loudly on the missing remote, never silently).
2. Review the Gate B evidence below and the DECISIONS.md Stage 2 entries.
3. **Deferred backup follow-up — personal rclone client_id:** create a personal Google OAuth `client_id`/`client_secret` and reconfigure the `gdrive` remote to use it (`rclone config` → edit `gdrive` → set `client_id`/`client_secret`). rclone's built-in shared client_id is **being retired during 2026**; once it is, the shared-key remote stops authorizing and nightly offsite copies fail. Do this before the retirement, not after.
4. **Deferred backup follow-up — GitHub repo mirror auth (still open):** `~/.local/bin/gh auth login --web --git-protocol https` remains unrun, so the private-repo code mirror is not yet pushing. Until it is done, code offsite = local git only. (Same auth step referenced in open item 1.)

## Gate B — Simulation Integrity (Stage 2 exit)

- [x] **Ledger determinism:** formal replay test — same snapshot + config + code + approved orders ⇒ bit-identical canonical result (pure + DB-path + real-data; `tests/engine/test_replay.py`, `test_gate_b_db.py`).
- [x] Cash, dividends, splits, fills, NAV reconcile: `nav == cash + positions_value` asserted every session in the loop and re-checked in tests; dividend/split scenario tests pass.
- [x] Four controls run on identical timing + cost assumptions — verified by test (benchmark fills route through the same `fill_price`/tiers; benchmarks step every session).
- [x] **Quarantine exercised end to end:** unsupported action freezes-and-marks → data-integrity breaker → classified incident → human-resolution branch AND timeout force-liquidation with `contains_forced_resolution` taint-forward on real `daily_nav` rows.
- [x] Four `ExitType`s produce correct effects: `forecast_resolution_effect` is the single authority; PRICE_STOP/VALUATION_EXIT resolve nothing (tests).
- [x] Fills: raw prices at next valid session open, correctly-signed adverse slippage, `slippage_applied` recorded.
- [x] Cluster risk budgeting respects §8 caps; caps-bind test drives a correlated basket into the theme cap.
- [x] `price_bars` provably rebuildable bit-identically from immutable documents; every row references a stored hash.
- [x] Hypothesis property tests (ledger conservation, identity+determinism), scenario tests (CAs, exit types), idempotency preserved (migrations, adapters, persist).
- [~] Backups: nightly `pg_dump|gzip` → local rolling dir ✓, systemd timer live ✓, **restore test passed** ✓; Drive offsite + GitHub remote **pending the human auth steps above** (open item 1).
- [x] `STATUS.md` / `DECISIONS.md` updated (price_bars ruling, FIFO lot rule, R2→Drive substitution, restore-isolation lesson).

## Gate A (Stage 1) — complete, human-reviewed 2026-07-17

All ten boxes held; see git history for the detailed checklist.
- **Done:** persistence layer (Compose dev DB, idempotent migrations, invariant-enforcing psycopg repository); ingestion adapters for Tiingo (replaced Stooq — see DECISIONS.md), FRED/ALFRED, and EDGAR with immutable sha256-keyed caching and exchange_calendars eligibility; pure-function analytics (exposure, valuation, quarantined momentum/volatility); pinned-Ollama inference wrapper logging every call to `agent_runs`; the four LLM roles with a tested blinding boundary; deterministic decision-packet renderer mirrored to the Obsidian vault (`~/obsidian-brain/TradingDesk/packets/`). Two full slice runs executed (23 successful LLM calls, zero failures); 122 tests pass.
- **Slice outcome (honest):** both runs ended in reviewer **REJECT** — evidence insufficient to establish data-center-specific exposure purity; run 2's reviewer additionally used the quarantined momentum metrics to flag that price action may be driving the narrative. Both rejections logged to `research_genealogy` with do-nothing cases. An evidence-insufficient abstention is a valid, rewarded outcome by design.
- **Open items for the human:**
  1. Review the decision packet: `reporting/output/decision-packet-grid-constraint-2026-07-17.md`.
  2. Backup wiring (restic → R2) now matters — the DB holds real research data. Needs R2 credentials; not wired yet.
  3. ufw: nothing needed — Postgres remains localhost-bound.

## Gate A — Data & Research Integrity (Stage 1 exit)

- [x] Every assertion in the decision packet traces to a source citation or a named Python function+version (provenance appendix + `tests/test_gate_a.py`).
- [x] Point-in-time eligibility enforced with a test (`eligible_documents` gate; run 1 demonstrably excluded same-day FRED vintages whose eligibility was 4 minutes in the future).
- [x] Basket construction demonstrably blinded to price history: analyst context is bit-identical with and without price data (`tests/test_blinding.py`); quarantined content raises `BlindingViolation`.
- [x] Raw responses cached immutably; extractions reference stored hashes; re-runs insert zero duplicate documents (live double-runs for all three adapters + tests).
- [x] Vendor/source discrepancies classified, not silently resolved (`data/reconcile.py`; a real `INCONSISTENT` incident fired on run 1's unit-mismatched figures).
- [x] Thesis changes versioned (v1 DRAFT → v2 verdict, parent-linked); pre/post-review versions persisted; both rejected hypotheses logged to `research_genealogy`.
- [x] Forecasts recorded with resolution criteria, horizons, and exit-type-aware semantics (6 per run; schema CHECKs + tests).
- [x] `agent_runs` logs model tag@digest + prompt version for every LLM call (23/23 successful, all pinned to `qwen3:30b-a3b@ad815644918f`).
- [x] Grid-constraint slice produced a complete human-readable decision packet in the Obsidian vault.
- [x] `STATUS.md` and `DECISIONS.md` updated; env re-verifications done (ufw n/a — localhost only; R2/restic raised above as an open item).

## Stage 0 exit check (complete 2026-07-17, human-reviewed)

All seven boxes checked — see git history for the detailed Stage 0 checklist.
