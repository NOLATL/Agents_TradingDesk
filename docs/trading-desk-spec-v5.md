# AI Trading Desk — Founding Spec (v5)

*Supersedes v4. Incorporates second external review (Gemini) + synthesis. Founding document for Claude Code handoff.*
*Not financial advice. Paper only. No live trade without explicit human approval.*

---

## 0. What Changed From v4 (and why)

| Change | Reason |
|---|---|
| One "invalidation" concept → **four exit types** (`PRICE_STOP` / `CAUSAL_BREAK` / `VALUATION_EXIT` / `TIME_STOP`) | v4 coupled market noise to thesis truth, which would poison calibration scoring. A stopped-out trade must not resolve a thesis's forecasts as false |
| Implicit source retention → **immutable raw-response caching for all web/API research** | Can't cite a document you didn't keep; also the backbone of injection defense and cost control |
| "bit-for-bit replayable" (overclaimed) → **ledger-deterministic + research-reproducible-by-artifact** | Re-running an LLM is not deterministic. Ledger replay is; research replay is from stored artifacts, not by re-executing the model |
| Correlation *cap* only → **cluster risk budgeting** (theme gets a fixed share of portfolio risk, split inverse-vol within; Ledoit-Wolf shrinkage for cluster ID) | A single-name cap doesn't see a 3-name theme stacking into one bet. But a full covariance optimizer overfits noise on 6–10 names — same reason Kelly was killed |
| "quarantine the position" (undefined for NAV) → **freeze-and-mark → breaker → human resolution → bounded auto-liquidation with NAV taint flag** | v4 never said what happens to NAV during quarantine. Silently liquidating fabricates a trade; freezing indefinitely distorts benchmarks. Neither is acceptable |
| Survivorship note → **analyst blinding rule** (price history barred from basket construction) | The real risk isn't the universe file (forward-only S&P is unbiased going forward); it's the *model's memory* of which names worked, which also collapses the control benchmarks if the analyst sees price performance |
| **Unchanged from v4:** calibration-primary, 4 LLM roles, forward-run controls, capability gates, deterministic core, Postgres/Docker/systemd day one | Validated by review; no change warranted |

---

## 1. Mission & Philosophy

An always-on thematic research and paper-trading desk on the Framework Desktop (Ryzen AI Max+ 395, 64GB, Ubuntu). It researches secular macro themes, decomposes them into monitorable causal chains with falsifiable forecasts, proposes baskets, paper-trades them through an internal deterministic simulator, and reports — with the human approving every order.

**Prime directive:** LLMs never produce a number that gets acted on. All signals, sizing, fills, risk checks, accounting, and metrics are deterministic, tested Python. LLMs do evidence extraction, thesis construction, review, and writing.

**Provenance is a first-class data model, not a principle.** A structured hallucination is still a hallucination. Every assertion in the system carries a type:

- `SOURCE_FACT` — extracted from a document, with citation + timestamps
- `COMPUTED` — produced by a named Python function, with version/commit
- `JUDGMENT` — LLM opinion, explicitly labeled, never an input to a calculation
- `HUMAN` — decision or override, with timestamp and rationale

**Model split:** local Ollama models run everything automated (zero marginal cost). Claude Code / Codex (subscriptions) are supervisors — planning, code, debugging, weekly review — never in the automated loop.

*Hardware validated: Qwen3-30B-A3B runs GPU-resident (100% GPU, ~71 tok/s, 21GB of 64GB) on the Radeon 8060S. Concurrent second model (router or coder) fits comfortably. The local-hot-loop premise holds on real silicon.*

**The central question being tested:** does an LLM-driven thematic research process produce *well-calibrated* judgments and beat naive controls? If not, the desk is a research/reporting assistant with rules-based execution — an acceptable outcome, not a failure.

---

## 2. Decision Register

| # | Decision | Value |
|---|---|---|
| 1 | Universe | S&P 500 (current constituents — used **forward only**, so no survivorship issue; basket construction blinded to price history, see §7) |
| 2 | Paper capital | $10,000 |
| 3 | Positions | **6–10** concurrent (research capacity is the binding constraint, not diversification math) |
| 4 | Risk limits | 10% max single name; **25% max theme; 30% max sector; cluster risk budget; max portfolio beta**; 100% deployable |
| 5 | Sizing | **Cluster risk budgeting**: each theme/cluster gets a fixed share of portfolio risk; inverse-vol within cluster; Ledoit-Wolf shrinkage for cluster identification. **No Kelly, no covariance optimizer** (both overfit) |
| 6 | Breakers | Four independent: market-risk, data-integrity, operational, model-quality (§8) |
| 7 | Benchmarks | **Run forward in-simulator, not backtested:** SPY, equal-weight S&P proxy, top-10 momentum control, and **"same basket, no LLM timing"** (equal-weight, fixed rebalance) |
| 8 | Primary metric | **Forecast calibration (Brier / calibration curves)**, then control-relative return. P&L over 6 months is treated as noise |
| 9 | Exit types | `PRICE_STOP` (trade dies, thesis lives) · `CAUSAL_BREAK` (thesis dies, basket liquidates) · `VALUATION_EXIT` (profit taken, thesis lives) · `TIME_STOP` (horizon hit, forecasts → UNRESOLVED) — see §5 |
| 10 | Rebalance | Weekly proposal cycle + event-driven exceptions (exit triggers) |
| 11 | Attribution | `strategy_tag` on every thesis/order/trade; post-mortems score thesis vs. trade correctness separately (§9) |
| 12 | Simulator | Internal Python engine, event-sourced. **Ledger-deterministic** (hard test); research **reproducible-by-artifact**. Broker paper account = **pre-live integration gate only** |
| 13 | Stack (day one) | Postgres, Docker Compose, systemd, Ollama, Python/Pydantic |
| 14 | Deferred to complexity triggers | LangGraph, Langfuse, Next.js dashboard, Evidently, shorts, tax engine, options/futures |
| 15 | Costs modeled | **Buy at open + adverse slippage; sell at open − adverse slippage**; tiered by liquidity/volatility, **calibrated against observed opens**; $0 commission; illustrative tax accrual only |
| 16 | Ledger | Lot-aware, wash-sale-**candidate** aware. Not tax-authoritative |
| 17 | Calendars | `exchange_calendars` everywhere. Never assume Mon–Fri |
| 18 | Model pinning | Ollama models + prompts versioned by hash; logged on every run |
| 19 | Research caching | Every web/API research response cached immutably to `documents` on first fetch; extractions reference the stored hash (§3.8) |

---

## 3. Data Model (build these schemas first — they cannot be retrofitted)

### 3.1 Bitemporal / eligibility columns (every source record)
```
event_time                    -- when it happened in the world
publication_time              -- when the source published it
ingestion_time                -- when we learned it
first_eligible_decision_time  -- derived: acceptance ts → market TZ → next valid session
source_revision_time          -- when the source last revised this record
```
Rule: no agent or service may read a record whose `first_eligible_decision_time` is in the future relative to the simulation clock. This replaces "filing date + 1."

For macro data, use **ALFRED-style vintages** wherever revisions matter (GDP, employment). Current-series FRED retrieval is look-ahead for historical work.

### 3.2 Provenance
```
assertion(id, type[SOURCE_FACT|COMPUTED|JUDGMENT|HUMAN], value,
          source_doc_id, citation_span, function_name, code_version,
          model_tag, prompt_version, created_at)
```

### 3.3 Forecasts (the measurement instrument)
```
forecast(id, thesis_id, statement, probability, horizon_end,
         resolution_criterion, resolved_at,
         outcome[TRUE|FALSE|UNRESOLVED],
         resolution_cause[MILESTONE|TIME_STOP|CAUSAL_BREAK|MANUAL],
         brier_score, created_by, model_tag, prompt_version)
```
Every thesis must produce **6–10 resolvable forecasts** at creation. "70% probability hyperscaler aggregate capex guidance remains above $X through the next two reporting cycles" — not "high confidence in AI infrastructure demand."

**Forecast resolution is decoupled from trade exit** (this is the core v5 fix): a `PRICE_STOP` or `VALUATION_EXIT` closes a *trade* but leaves the thesis and its forecasts **active and still scoring**. Only `CAUSAL_BREAK` (milestone falsified) resolves forecasts as FALSE; only reaching `horizon_end` resolves them (TRUE/FALSE by criterion) or marks UNRESOLVED via `TIME_STOP`. Market noise must never enter the Brier score.

### 3.4 Experiment registry
Freezes, per experiment: universe def, data snapshot, prompt versions, model hashes, code commit, config, cost assumptions, portfolio rules, benchmark rules, evaluation window, **pre-declared success criteria**.

### 3.5 Research genealogy
Every hypothesis generated — including **rejected ones**, with reason, variant count, and whether evidence was added *after* observing price action. Selection across many themes is multiple hypothesis testing whether or not you call it that.

### 3.6 Data contracts (one YAML per source, not a platform)
Per field: raw/adjusted, currency, timezone, session date, corporate-action treatment, source priority, revision policy, expected publication delay, null rule, **license and permitted-use**.

### 3.7 Core tables
`documents` (immutable raw), `extractions`, `theses`, `forecasts`, `milestones`, `orders`, `trades`, `tax_lots`, `positions`, `daily_nav`, `benchmarks_nav`, `lessons`, `agent_runs`, `data_incidents`, `experiments`.

### 3.8 Research response cache (immutable)
Every web search / API research response is serialized to `documents` on **first fetch**:
```
documents(id, content_hash, raw_bytes, extracted_text, retrieval_url,
          query_params, source_name, event_time, publication_time,
          ingestion_time, license_note, is_raw_immutable=TRUE)
```
- Raw bytes stored once, hashed, never mutated. Extractions point at `content_hash`.
- **Date-bounded queries** (defense-in-depth): research queries append date filters tied to the simulation clock where the source supports them. Treated as a guardrail, not a guarantee — search engines honor date filters unreliably, and in live forward trading the clock is *now*.
- **Replay semantics:** the ledger replays deterministically from stored data + config + approved orders. Research does **not** re-execute the LLM; it replays from the stored decision packet, evidence, and model/prompt hashes. You can always reproduce *what was decided and on what basis*; you cannot promise the model would phrase it identically.

---

## 4. LLM Roles (four, not fifteen)

| Role | Does | Does NOT |
|---|---|---|
| **Evidence Collector** | Retrieve filings/transcripts/news; cache raw responses immutably; extract claims with citations + timestamps; emit `SOURCE_FACT` assertions only | Interpret, rank, recommend, or call tools from untrusted document context |
| **Research Analyst** | Build/update thesis objects: causal chain, exposure basket, valuation check, milestones, **probabilistic forecasts**, exit criteria | Compute any number; propose sizes; place orders; **see price-performance history** (§7 blinding) |
| **Independent Reviewer** | **Audit** the thesis: hunt disconfirming primary evidence, challenge exposure mapping and causal timing, compare valuation to peer/historical distributions, ask whether price action is driving the narrative, produce an explicit **do-nothing case** | Merely write a pessimistic narrative |
| **Report Assembler** | Turn validated objects into daily/weekly markdown | Add new claims |

The reviewer sees only the **evidence packet and the analyst's output** — not the analyst's reasoning trace — to reduce correlated framing errors. Log **pre- and post-review** thesis versions so you can measure whether review improves calibration or just adds text.

**Everything else is a deterministic Python service, not an agent:** risk checks, position sizing, portfolio construction, execution/simulator, benchmark controls, monitoring, data reconciliation, metrics. LLMs may *narrate* their outputs; they may not produce them.

Split roles further **only when tests show it improves output quality.**

---

## 5. Thesis Structure & Exit Types

Each thesis (e.g. AI buildout, nuclear/power, data centers, housing, aging population) requires at creation:

1. **Causal chain** — explicit links: AI capex → data center construction → grid strain → turbine/nuclear/transformer demand → revenue at named companies.
2. **Forecasts** — 6–10 resolvable probabilistic statements with horizons and resolution criteria (§3.3).
3. **Exposure basket** — S&P 500 names ranked by *measured* exposure purity: % of revenue, backlog, segment profit, capex, or management emphasis tied to the thesis — from filings/transcripts, **never from price performance** (§7).
4. **Valuation check** — is it already priced in? (Right theme, wrong price is the classic thematic failure.)
5. **Exit criteria** — all four types defined at creation, deterministic:

| Exit type | Trigger | Trade effect | Thesis effect | Forecast effect |
|---|---|---|---|---|
| `PRICE_STOP` | Adverse price move / idiosyncratic single-name news unrelated to the chain | Close that position | **Thesis stays active** | **Keep scoring** |
| `CAUSAL_BREAK` | A causal-chain milestone is falsified | Liquidate the basket | **Thesis invalidated** | Resolve per criterion (typically FALSE) |
| `VALUATION_EXIT` | Thesis correct and now fully priced in | Close at profit | **Thesis stays valid** | **Keep scoring** to horizon |
| `TIME_STOP` | `horizon_end` reached, unresolved | Close position | Thesis closed | Mark UNRESOLVED |

6. **Do-nothing case** — from the reviewer. **"Evidence insufficient, take no position" is a valid, tracked, rewarded output — not a pipeline failure.**

**Governance:** 6–10 concurrent positions; monthly cull of unresolved theses; every thesis carries all exit criteria from birth.

---

## 6. Simulator & Ledger

One event-sourced engine. **Acceptance test (Gate B): given the same data snapshot, config, code version, and approved orders, the engine reproduces an identical ledger and NAV.** (This is the *ledger* determinism guarantee; it does not extend to LLM outputs — see §3.8.)

```
engine/
  portfolio.py    # positions, cash, cost basis, mark-to-market
  execution.py    # buy@open+adverse, sell@open−adverse; tiered slippage by ADV
                  # participation, spread, volatility, gap, event status
  sizing.py       # cluster risk budgeting: theme→risk share, inverse-vol within;
                  # Ledoit-Wolf shrinkage for cluster identification (no optimizer)
  ledger.py       # immutable trades; lot IDs, acquisition dates, holding periods,
                  # realized P&L, wash-sale CANDIDATE flags
  quarantine.py   # freeze-and-mark → breaker → human → bounded auto-liquidation
  controls.py     # SPY / equal-weight / momentum / same-basket-no-LLM-timing,
                  # run forward on identical timing + cost assumptions
  metrics.py      # Sharpe, Calmar, max DD, hit rate, per-tag attribution,
                  # Brier scores, calibration curves, PSR, bootstrap CIs
```

**Slippage:** start with a conservative tiered model; **calibrate it** by comparing modeled fills against actual subsequent opens/quotes as paper orders accumulate.

**Position sizing — cluster risk budgeting (not covariance optimization):** assign each theme/cluster a fixed share of total portfolio risk, then divide that share among cluster members by inverse volatility. Correlations enter via *cluster membership* (identified using Ledoit-Wolf-shrunk correlations), not via inverting a noisy covariance matrix. This gets the concentration protection Gemini flagged without the estimation-error blowup a small-sample optimizer would introduce.

**Corporate actions — the quarantine protocol.** Support splits, ordinary dividends, and symbol changes natively in Stage 1. Everything else — mergers, cash/stock acquisitions, spin-offs, special dividends, rights offerings, delistings, halts, bankruptcy distributions, cash-in-lieu — is **not silently computed**. Instead:

1. **Freeze & mark:** the position is marked at its last valid close and frozen (no new orders). NAV continues using the frozen mark, so the timeline doesn't distort.
2. **Breaker fires:** the data-integrity breaker raises a classified `data_incident` and alerts the human.
3. **Human resolves:** within a bounded window, the human posts an adjustment entry (`HUMAN` provenance + rationale) reflecting the true corporate action.
4. **Fallback:** if unresolved past N days, the engine force-liquidates to cash at the last valid close **and sets a `contains_forced_resolution` taint flag on the affected NAV series**, so no performance claim can unknowingly rest on a fabricated fill.

Determinism is preserved (the fallback is a deterministic rule), and nothing is silently invented.

**Tax:** lot-aware and wash-sale-*candidate* aware. Post-tax figures displayed only under an explicit label: *"Illustrative accrual using configured marginal rates; not tax-reporting output."* Authoritative treatment deferred to broker records and tax software.

---

## 7. Universe Integrity: Blinding, Not Constituent Arrays

**The forward-only S&P 500 universe is not survivorship-biased.** Survivorship bias is a *performance-measurement* error — computing historical returns on a universe that excludes the dead. This desk computes no such returns; it chooses, today, among stocks actually in the index and actually purchasable today. Their forward return distribution is not contaminated by past failures elsewhere. Point-in-time constituent arrays would not fix the real problem anyway, because the real problem is in the model's weights, not in the universe file.

**The real risk is hindsight/narrative contamination (contamination Class 3/4 applied to basket construction).** The model already knows which AI-theme names worked in 2024–25. If the analyst is shown price performance while ranking exposure purity, its "analysis" may be laundered hindsight — *and* the control benchmarks collapse, because an analyst that has absorbed 12-month price rankings is running momentum with extra steps, destroying the independence of the momentum and same-basket-no-LLM-timing controls.

**The fix is blinding, enforced at the evidence-collector boundary:**
- Basket inclusion and exposure ranking are justified **only** by measured exposure metrics — % of revenue, backlog, segment profit, capex, management emphasis — from filings and transcripts.
- **Historical price performance is barred as an input to basket construction.** Not discouraged — barred, and enforced by what the evidence collector is permitted to place in the analyst's context.
- Any thesis whose basket cannot be justified without price history is flagged Class 3 and excluded from performance claims.

**Contamination classes (applied to the LLM, not to backtests):**

- **Class 0** No LLM involvement in method or historical input
- **Class 1** LLM-generated generic methodology, frozen before testing
- **Class 2** LLM analyzes historical documents, blinded to dates/names where feasible
- **Class 3** LLM analyzes named historical events inside its training window
- **Class 4** LLM references subsequent outcomes

Only 0–1 support performance claims. **This argues for weighting theses toward genuinely unresolved forward questions** — the strongest validation is forward testing on events after the model's knowledge horizon with prompts frozen.

**Why backtesting mostly disappears:** you cannot backtest "AI capex drives grid demand" — one instance, unfolding now. The v3 backtest machinery (vectorbt, point-in-time constituents, survivorship haircuts, validity harness) existed mainly to serve the control benchmarks, and **the controls don't need history: they run forward in the simulator from day one.** Those tools are deferred; they return only if a quantitative sleeve that genuinely needs history is ever added.

---

## 8. Risk Controls (all deterministic; LLMs cannot modify)

**Exposure limits:** 10% single name · 25% theme · 30% sector · cluster risk budget · max portfolio beta · gross/net · liquidity (ADV participation) · earnings-event concentration.

**Four independent breakers:**

| Breaker | Trips on | Response |
|---|---|---|
| Market-risk | Portfolio drawdown from high-water mark (EOD), volatility event | Block new risk; no forced liquidation; human decides |
| Data-integrity | Missing, stale, revised, or inconsistent data; **unsupported corporate action** | **Halt order generation entirely.** Late/failed data must never produce orders. Triggers quarantine protocol (§6) |
| Operational | Incomplete pipeline, failed reconciliation, duplicate run | Halt + alert; idempotent recovery |
| Model-quality | Malformed output, ungrounded claims, low confidence, schema violation | Reject the proposal; no order reaches the human |

Each breaker specifies: watermark, EOD-only evaluation, whether it blocks new orders vs. forces de-risking, reset condition, and override path (human, logged).

**Human approval:** every order, always, including paper. The approval screen shows **the evidence packet**, not just agent reasoning. Log the original proposal and any human modification separately so agent performance, human-overlay performance, and combined performance can be distinguished.

**Content quarantine (prompt injection):** immutable raw-source storage (§3.8); sanitized extracted text; source allowlists; document-type validation; **no tool calls from untrusted document context**; strict output schemas; claim-level citations; max document/field sizes; instruction-like-text anomaly detection; **separate model contexts for extraction vs. action proposal**; hashed decision artifacts.

---

## 9. Measurement (the actual experiment)

**Primary — calibration.** 10 theses × 6–10 forecasts ≈ **60–100 scored predictions in six months**, versus 10–30 trades. This is the only statistically evaluable signal available on this timeline. Track Brier scores, log loss, and calibration curves by decision type. *Do the desk's 70% claims resolve near 70%?* (Now protected from market noise by the exit-type decoupling in §5.)

**Secondary — control-relative performance.** vs. SPY, equal-weight, momentum, and **same-basket-no-LLM-timing**. This last control isolates where value comes from: theme selection vs. security selection vs. timing vs. sizing vs. human overrides vs. plain market exposure. (Its validity depends on the §7 blinding rule holding.)

**Tertiary — process integrity (near-binary checklist).** No look-ahead. No LLM-generated number entered a decision. Risk limits enforced every time. Exit triggers fired correctly and with the right type. Fills matched model. Reports accurate and on time. Ran unattended without silent failure. Restores actually tested.

**No-trade quality.** Track abstentions, reasons, subsequent outcomes, and whether abstaining was correct. The desk likely creates more value avoiding weak trades than finding great ones.

**Post-mortems score thesis correctness and trade correctness separately:** causal accuracy · milestone accuracy · exposure mapping · valuation judgment · entry timing · sizing · exit discipline (right exit type?) · execution · human-override effect. Otherwise lesson retrieval teaches the wrong lesson.

P&L over six months is **noise** and is reported as such.

---

## 10. Capability Gates (replace the calendar)

Gates govern **advancement**. Time-boxes govern **scope reassessment**: if a gate isn't met inside its box, that is a signal the gate is too ambitious and scope must be cut — not a signal to keep grinding.

### Stage 0 — Define the Experiment *(time-box: 1 week, no code)*
Objective, definition of "better research," frozen benchmarks, contamination classes, thesis/claim/forecast/experiment schemas, exit-type semantics, 1 starter theme + 2 candidates.
**Exit:** schemas written; success criteria pre-declared in the experiment registry.

### Stage 1 — Evidence Vertical Slice *(time-box: 6 weeks)*
Postgres + schemas (§3, incl. research cache 3.8), Docker Compose, systemd timers, Ollama pinned models, `exchange_calendars`, SEC/FRED/price adapters with data contracts, timestamp-eligibility logic, evidence collector (with immutable caching + blinding boundary) + analyst + reviewer + reporter, **one theme, five companies**, deterministic exposure/valuation/momentum/volatility modules, markdown **decision packet**.

**Gate A — Data & Research Integrity**
- Every assertion in a decision packet traces to a source citation or a named Python function.
- Point-in-time eligibility enforced; no post-event information in any packet.
- **Basket construction demonstrably blinded to price history** (§7).
- **Raw research responses cached immutably; extractions reference stored hashes.**
- Vendor discrepancies **classified** (split/dividend/bad print/symbol map/missing bar/calendar/revision), not generically quarantined.
- Thesis changes versioned; rejected hypotheses logged.
- Forecasts recorded with resolution criteria and exit-type semantics.

### Stage 2 — Deterministic Portfolio Laboratory *(time-box: 5 weeks)*
Event-sourced ledger (lots, splits, dividends), tiered execution model, **cluster risk budgeting**, **quarantine protocol**, the four forward-running controls, risk limits + four breakers, metrics incl. Brier/calibration.

**Gate B — Simulation Integrity**
- **Replay produces an identical ledger and NAV.** Formal acceptance test (ledger determinism only).
- Cash, dividends, splits, fills, NAV all reconcile.
- Controls use identical timing and cost assumptions as the live book.
- **Quarantine protocol exercised:** an unsupported corporate action freezes, alerts, and (on timeout) force-liquidates with the NAV taint flag set.
- Four exit types produce correct trade/thesis/forecast effects in tests.
- Property-based and scenario tests pass.

### Stage 3 — Forward Paper Workflow *(time-box: 12 weeks minimum — this is the experiment)*
Weekly proposal cycle, human approval with evidence packet, no-trade option, thesis/order versioning, override logging, milestone monitoring, operational alerts, post-mortems + lessons, slippage calibration against observed opens.

**Gate C — Operational Integrity**
- Restart/recovery tested; duplicate runs idempotent.
- Late/failed data never generates orders.
- **Backups actually restored** (scheduled restore test, clean-machine deploy test).
- Survived: model unavailability, disk-full, network outage, source schema change, timezone failure.
- 8–12 consecutive weeks of the full cycle with **no silent failures**.

**Gate D — Research Value (the go/no-go)**
- Calibration curves meaningful and improving; Brier beats a naive base-rate forecaster.
- Beats or matches the **same-basket-no-LLM-timing** control (else the LLM adds nothing over the basket).
- Beats the momentum control, or the honest conclusion is drawn.
- Abstention quality demonstrable.

### Stage 4+ — Earn Complexity
Adopt only on a **trigger**, never a date:
- **LangGraph** — when role count/branching makes plain Python + Pydantic painful
- **Langfuse** — when `agent_runs` + JSON artifacts stop being enough to debug
- **Next.js dashboard** — when markdown reports + a CLI approval flow become the bottleneck
- **Broker paper account** — **mandatory pre-live integration gate** (order lifecycle, rejections, partial fills, session rules) before the first real dollar
- **Shorts** — only after: borrow availability accessible, borrow fees modeled dynamically, corporate actions validated, gross/net/theme limits operational
- **Options / futures** — **treated as a separate project**, not an increment. Different data, valuation, lifecycle, margin, settlement, execution.
- **Tax engine, Evidently/drift monitoring, point-in-time constituents / vectorbt** — only if a use case demands them

---

## 11. Ops

Docker Compose (Postgres + services); systemd timers for daily ingestion, weekly proposal, post-close reporting; nightly `pg_dump` → second disk → restic → R2; **quarterly restore test, scheduled and logged**; secrets in env files now, sops/age before live; broker credentials never enter LLM context; Obsidian vault receives thesis notes, decision packets, post-mortems.

**Idempotency is a requirement, not a nicety:** every timer-driven job must be safe to run twice.

**ufw note:** the Framework runs ufw with SSH/3389/Samba allow-rules staged. Any new service that must be reachable (Postgres from another host, a dashboard port) needs a corresponding ufw allow rule — services bound to localhost for same-box Docker networking do not.

---

## 12. Known Risks (candid)

- Published LLM trading "alpha" is largely look-ahead/contamination artifact (FINSABER, Look-Ahead-Bench). Expect research leverage, not magic.
- Thematic theses resist statistical validation almost by definition. Discipline comes from decomposition, resolvable forecasts, calibration scoring, valuation checks, and post-mortems — not backtests.
- The model may already know how current narratives resolved through its training cutoff (contamination Classes 3–4). Weight toward genuinely unresolved forward questions, and enforce §7 blinding or the control benchmarks silently break.
- Two instances of the same model produce correlated reasoning; "debate" can be theater. Hence the auditing reviewer with evidence-only context and pre/post logging.
- The research-selection process itself can overfit (many themes generated, only pretty ones advance). Hence research genealogy.
- Free data has quality gaps; classification of discrepancies mitigates, never eliminates.
- Small-sample corporate-action handling: the quarantine protocol trades some realism for safety; the taint flag ensures you always know when a fabricated fill touched a NAV series.
- Six months cannot prove alpha. §9 defines what is actually being measured.
- **Most likely honest outcome:** a well-calibrated research and reporting desk that does not beat a momentum control. That is a successful experiment.
