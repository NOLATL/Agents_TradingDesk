# EXPERIMENT REGISTRY — EXP-001

*Frozen per spec §3.4 on 2026-07-17 (Stage 0). Changing any frozen value after
the Stage 3 forward run begins constitutes a NEW experiment id and re-enters
validation. The machine-readable twin will be the `experiments.registry` JSONB
row inserted at Stage 1 database bring-up; `config/experiment.yaml` is the
config baseline it hashes.*

## Identity

- **Experiment id:** EXP-001
- **Question:** Does an LLM-driven thematic research process produce well-calibrated probabilistic judgments, and does it beat naive forward controls?
- **Registered:** 2026-07-17, Stage 0 (no data ingested, no code beyond schemas)

## Universe

- Current S&P 500 constituents, used **forward only**; refreshed on index changes going forward. No historical constituent reconstruction (anti-scope).
- Basket construction blinded to price history (§7); any thesis whose basket cannot be justified without price history is flagged contamination Class 3 and excluded from performance claims.

## Data snapshot approach

- All research responses cached immutably to `documents` on first fetch (§3.8); the experiment's evidence base is the set of stored hashes.
- Price/macro data: nightly ingestion per `contracts/*.yaml`; FRED via ALFRED vintages for revision-prone series.
- Eligibility: every record's `first_eligible_decision_time` derived per its contract; nothing is readable before it (invariant 2).

## Models & prompts (pinned)

- Reasoning (all four roles): `qwen3:30b-a3b`, Ollama digest `ad815644918f` (verified locally 2026-07-17). Full sha256 recorded into `experiments.registry` at Stage 1 bring-up.
- Prompt versions: none yet (prompts are Stage 1 artifacts); each prompt is hash-versioned at creation and logged per run in `agent_runs`. A model tag/digest change = new experiment.

## Config baseline

- `config/experiment.yaml` (this commit). Its sha256 becomes `experiments.config_hash` at Stage 1 bring-up; `experiments.code_commit` records the git commit.

## Cost assumptions (Decision Register #15)

- Buy at open + adverse slippage; sell at open − adverse slippage; tiered by liquidity/volatility; calibrated against observed opens as paper orders accumulate; $0 commission; illustrative tax accrual only, clearly labeled.

## Portfolio rules (Decision Register #2–5, frozen)

- Paper capital $10,000; 6–10 concurrent positions; long-only, S&P 500 cash equities.
- Limits: 10% single name / 25% theme / 30% sector / max portfolio beta / 100% deployable.
- Sizing: cluster risk budgeting — fixed risk share per theme, inverse-vol within, Ledoit-Wolf shrinkage for cluster identification. No Kelly, no covariance optimizer.
- Weekly proposal cycle + event-driven exits; human approves every order.

## Benchmark rules (the four forward controls, Decision Register #7)

All run **forward in-simulator** on identical timing and cost assumptions as the live book — never backtested:

1. **SPY** — market exposure control.
2. **Equal-weight S&P proxy** — breadth control.
3. **Top-10 momentum** — the naive competitor (12-1 month total return, monthly rebalance, same costs).
4. **Same-basket, no-LLM-timing** — the desk's own baskets, equal-weight, fixed rebalance; isolates whether the LLM's timing/sizing adds anything over its security selection. Validity depends on §7 blinding holding.

## Evaluation window

- Stage 3 forward run: **12 weeks minimum, target 26 weeks (6 months)** of consecutive weekly cycles. Calibration is evaluated on all forecasts whose horizons resolve within the window; P&L over this window is treated as noise and reported as such.

## Pre-declared Gate D success criteria (frozen now so they cannot move later)

Evaluated at end of the evaluation window. The experiment **succeeds** if the process integrity criteria hold AND at least the calibration criterion is met:

1. **Calibration (primary):** ≥60 resolved forecasts; mean Brier score **beats a naive base-rate forecaster** (predicting the historical base rate of each forecast's reference class, or 0.5 where none exists) on the same set; calibration curve slope between 0.7 and 1.3 with no bucket off by >20 points on ≥20 samples.
2. **Same-basket control:** desk NAV ≥ same-basket-no-LLM-timing NAV, or the honest conclusion is drawn that LLM timing/sizing adds nothing beyond basket selection.
3. **Momentum control:** desk NAV ≥ top-10 momentum NAV, **or** the documented conclusion "well-calibrated research assistant that does not beat momentum" is drawn.
4. **Abstention quality:** every no-trade decision logged with reasons; in retrospective scoring, abstentions were correct (avoided a losing or thesis-violating trade) in ≥50% of resolvable cases.
5. **Process integrity (near-binary):** no look-ahead violations; no LLM-produced number entered any calculation or order; risk limits enforced on every proposal; exit types fired correctly; no silent failures across the run.

**The accepted null result, declared in advance:** *"The desk is well-calibrated (criterion 1) but does not beat the momentum control (criterion 3's honest branch)."* This outcome is a **successful experiment** — it answers the research question in the negative for alpha while validating the desk as a calibrated research assistant with rules-based execution. It will be reported exactly as such, not reframed.

**Failure**, equally pre-declared: criterion 1 misses (uncalibrated), or any process-integrity breach in criterion 5 — regardless of P&L. A profitable but integrity-breached run is a failed experiment.

## Multiple-hypothesis discipline

- Every theme/hypothesis generated — including rejected ones — is logged to `research_genealogy` with rejection reason, variant count, and whether evidence was added after observing price action. Reported alongside results so selection effects are visible.
