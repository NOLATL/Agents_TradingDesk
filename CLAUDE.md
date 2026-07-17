# CLAUDE.md — AI Trading Desk

*This is the project constitution. Read it fully at the start of every session before writing or changing code. The authoritative design is `docs/trading-desk-spec-v5.md`; this file is the operational contract that governs how you build it.*

---

## What this project is

An always-on **thematic research and paper-trading desk** running locally on a Framework Desktop (Ryzen AI Max+ 395, 64GB, Ubuntu 26.04). It researches secular macro themes, decomposes each into a causal chain with falsifiable probabilistic forecasts, proposes equity baskets, paper-trades them through an **internal deterministic simulator**, and reports — with a human approving every order.

**This is an experiment, not a money machine.** The question being tested is whether an LLM-driven research process produces *well-calibrated* judgments and beats naive controls. "It doesn't beat a momentum baseline, but it's a well-calibrated research assistant" is a **successful** outcome, not a failure. Do not optimize toward making the P&L look good; optimize toward making the *measurement honest*.

---

## The Prime Directive (never violate)

**LLMs never produce a number that gets acted on.** Every signal, price, weight, size, fill, risk check, P&L figure, and metric is produced by deterministic, tested Python. LLMs do four things only: extract evidence, construct theses, review theses, and write reports. If you ever find yourself about to let a model output a quantity that flows into a calculation or an order, stop — that is a design error, route it through Python instead.

A **structured hallucination is still a hallucination**. Pydantic-validating a model's numeric output does not make it trustworthy; it makes it well-formatted. Validation is not verification.

---

## Non-negotiable invariants

These are load-bearing. If a task seems to require breaking one, stop and raise it with the human rather than working around it.

1. **Provenance on every assertion.** Every stored fact carries a type: `SOURCE_FACT` (with citation + timestamps), `COMPUTED` (with function name + code version), `JUDGMENT` (LLM opinion, never an input to a calculation), or `HUMAN` (with rationale). No untyped assertions enter the database.

2. **Point-in-time eligibility.** No agent or service may read a record whose `first_eligible_decision_time` is in the future relative to the simulation clock. This is the anti-look-ahead guarantee and it is absolute.

3. **Ledger determinism.** Given the same data snapshot, config, code version, and approved orders, the simulator reproduces an identical ledger and NAV. This is a formal test (Gate B), not an aspiration. Note the scope: **ledger** replay is deterministic; **LLM** replay is not — research reproduces from stored artifacts (decision packet + evidence + model/prompt hashes), never by re-running the model.

4. **Immutable raw research.** Every web/API research response is serialized to the `documents` table on first fetch, hashed, and never mutated. Extractions reference the stored hash. You cannot cite what you did not keep.

5. **Analyst blinding.** Basket construction and exposure ranking are justified *only* by measured exposure metrics (revenue %, backlog, segment profit, capex, management emphasis) from filings/transcripts. **Historical price performance is barred from basket construction** — enforced at the evidence-collector boundary. This protects both against hindsight contamination and against the control benchmarks silently collapsing into momentum.

6. **Human approval gate.** Every order — including paper orders — stops at a human approval step showing the evidence packet. No code path places an order without it. Original proposal and any human modification are logged separately.

7. **Forecast/trade decoupling.** A trade exit must never resolve a thesis's forecasts unless the exit is a `CAUSAL_BREAK` or a horizon resolution. `PRICE_STOP` and `VALUATION_EXIT` close trades but leave forecasts scoring. Market noise must never touch the Brier score.

8. **Deterministic risk & quarantine.** Risk limits and the corporate-action quarantine protocol are Python rules an LLM cannot modify. Unsupported corporate actions freeze-and-mark, never silently compute.

---

## Architecture (build order matters)

Layered, and lower layers must be solid before higher ones are built:

- **Layer 0 — Data & analytics (deterministic Python):** ingestion adapters, Postgres system of record, pure-function analytics (exposure, valuation, momentum, volatility, sizing, metrics). Built and tested first.
- **Layer 1 — Local inference:** Ollama, pinned models, OpenAI-compatible endpoint. Tool calls wrapped with parse-and-retry (Ollama leaks tool calls into `content` on some models).
- **Layer 2 — Agent roles:** four LLM roles (Evidence Collector, Research Analyst, Independent Reviewer, Report Assembler) as plain Python + Pydantic. **Not LangGraph yet** — that is earned later.
- **Layer 3 — Supervisor:** you (Claude Code) + the human. Not part of the automated loop.
- **Layer 4 — Reporting & control:** JSON → markdown decision packets and reports; Obsidian vault; CLI approval flow. **No web dashboard yet.**

**Everything that is not one of the four LLM roles is a deterministic service, not an agent:** risk, sizing, portfolio construction, execution/simulator, controls, monitoring, reconciliation, metrics.

---

## What NOT to build (anti-scope list)

The spec fought hard to stay small. Do not add these until their explicit trigger fires (see spec §10, Stage 4+):

- ❌ **LangGraph** — until role branching makes plain Python painful
- ❌ **Langfuse** — until `agent_runs` + JSON stop being enough to debug
- ❌ **Next.js / any web dashboard** — until markdown + CLI approval is the bottleneck
- ❌ **Shorts, options, futures** — separate future projects, not increments
- ❌ **Broker/Alpaca/IBKR integration** — the simulator is internal; a broker is a *pre-live integration gate only*
- ❌ **Backtesting engines (vectorbt etc.), point-in-time constituent data** — controls run *forward*, not historically
- ❌ **Tax engine, drift monitoring (Evidently)** — only if a use case demands them
- ❌ **Covariance/portfolio optimizers** — sizing is cluster risk budgeting, not optimization (optimizers overfit small samples)

If a task tempts you toward any of these, it is almost certainly the wrong task. Raise it.

---

## Gates govern advancement; time-boxes govern scope

Do not advance stages by calendar. Advance only when the current gate's criteria are met. If a gate isn't met within its time-box, that is a signal the **gate is too ambitious and scope must be cut** — not a signal to grind harder. Current stage and gate status live in `docs/STATUS.md` (create and maintain it).

---

## Coding standards

- **Python tooling:** you choose (uv / poetry / venv) at Stage 0, record the choice and rationale in `docs/DECISIONS.md`, and stay consistent. Prefer the simplest thing that gives reproducible locked dependencies.
- **Analytics are pure functions** — deterministic, no hidden state, no network calls inside them. They take data in, return values out, and are unit-tested against known inputs.
- **Pydantic schemas** for every cross-boundary object; one schema module, imported everywhere. Schema changes are versioned.
- **Every timer-driven job is idempotent** — safe to run twice. This is a requirement, not a nicety.
- **Tests are not optional for Layer 0.** Property-based tests (Hypothesis) for the engine; scenario tests for corporate actions and exit types; the ledger-determinism replay test is a first-class acceptance test.
- **Small, scoped commits** with clear messages. Prefer many small PRs over large ones so the human can review.
- **No secrets in code or in any LLM context.** Env files now; broker credentials never touch a model prompt.
- **Config over hardcoding** — risk limits, slippage tiers, thresholds, model tags all live in versioned config, not scattered literals.
- **Log model tag/hash and prompt version on every agent run** into `agent_runs`.

---

## Local models (validated on this hardware)

- Reasoning workhorse: `qwen3:30b-a3b` — GPU-resident, ~71 tok/s, 21GB. This runs the four roles.
- Coding (local, optional): `qwen3-coder:30b`.
- Raise Ollama context above the 32K default before feeding filings — long 10-Ks will silently truncate otherwise. Chunk + retrieve rather than stuffing when a document is very long.
- Pin model tags per experiment; a model change is a new experiment and re-enters validation.

---

## Environment (correct these if wrong — do not assume)

Confirm at Stage 0 and record actuals in `docs/DECISIONS.md`:
- Host: `framework` (Ubuntu 26.04), reached over Tailscale SSH.
- Postgres: installed via apt during setup. **Decide and record** whether the project uses that system Postgres or a Docker-Compose Postgres container; the spec assumes Compose for portability — confirm before wiring connection strings.
- Docker + Docker Compose v2 present; user in `docker` group.
- `ufw` is active with SSH/3389/Samba allow-rules. Any service that must be reachable from another host needs an allow rule; localhost-bound Compose services do not.
- Obsidian vault path: `/home/jared/obsidian-brain` (confirmed Stage 0); desk output goes to `TradingDesk/` at vault root. The vault is covered by Obsidian sync — decision packets and notes mirrored there are offsite-replicated by that mechanism.
- Offsite DB backup: **Google Drive via `rclone`** (replaced the original Cloudflare R2/restic plan — see `docs/DECISIONS.md` and `docs/STAGE_2_KICKOFF.md`). Nightly `pg_dump | gzip` to a local dir on the 1TB card, then `rclone copy` of the sealed dump to the Drive remote; never live-sync the data dir. rclone config/token stays outside any LLM context. Code is covered by a private GitHub remote.

---

## When you are unsure

Ask the human. Specifically raise: anything that would break an invariant, anything on the anti-scope list, any place where you'd otherwise let a model produce a number, and any environment assumption you can't verify. A paused question is cheaper than a wrong foundation.
