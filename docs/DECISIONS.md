# DECISIONS

Decision log: tooling, environment actuals, rationale. Append-only; newest at the bottom of each section.

## 2026-07-17 — Environment actuals (verified on the machine)

| Assumption (CLAUDE.md) | Actual | Action |
|---|---|---|
| Host `framework`, Ubuntu 26.04, Tailscale SSH | Confirmed: `framework`, Ubuntu 26.04 LTS | — |
| Postgres via apt; decide system vs. Compose | System Postgres **18.4 installed and running** (apt) | **Decision: Docker Compose Postgres** for this project (human-confirmed). Pinned version, isolated from other projects' system DB, localhost-bound so no ufw rule needed. System Postgres left untouched. |
| Docker + Compose v2, user in `docker` group | Confirmed: Docker 29.1.3, Compose v2.40.3, user in group | — |
| ufw active, SSH/3389/Samba rules | Not re-verified this stage; no reachable service is created in Stage 0 (scratch Postgres binds localhost) | Re-verify at Stage 1 when services appear |
| Obsidian vault path: confirm | **`/home/jared/obsidian-brain`** (contains Economics/, synced) | Recorded; nothing is written to the vault in Stage 0 |
| Python version | **3.14.4** (system CPython; used as the uv project interpreter) | — |
| Ollama models | `qwen3:30b-a3b` and `qwen3-coder:30b` present locally | Tags pinned in EXPERIMENT_REGISTRY.md; unused in Stage 0 |
| Cloudflare R2 / restic backup | Not verified this stage; nothing to back up yet | Verify at Stage 1 when the database holds data |

## 2026-07-17 — Repo location

Kickoff sketches a `trading-desk/` directory; docs already lived in `/home/jared/projects/Agents_TradingDesk/docs`. **Decision (human-confirmed): build in-place in `Agents_TradingDesk`**, matching the machine's `Agents_*` project convention. `git init` performed here; `CLAUDE.md` moved to repo root per the kickoff layout.

## 2026-07-17 — Python tooling: uv

**Choice: uv** (installed 0.11.29). Rationale: single binary, fast, produces a true lockfile (`uv.lock`) with no extra config, and `uv run` gives a reproducible entry point — the simplest of the three options (uv/poetry/venv) that yields locked deps. Poetry adds ceremony without benefit at this scale; bare venv + pip has no first-class lock.

Stage 0 dependencies (locked in `uv.lock`): `pydantic`, `psycopg[binary]`, `pyyaml` (contracts are YAML and tests read them); dev: `pytest`.

**Deliberately not added:** SQLAlchemy. The spec keeps SQL DDL as plain `.sql` files; round-trip tests apply the DDL to a scratch Postgres via psycopg and introspect `information_schema` to compare against the Pydantic models. An ORM layer is not needed for that and would become a second source of schema truth.

## 2026-07-17 — Primary EOD price source: Stooq

**Decision (human-confirmed): Stooq** for Stage 1 daily OHLCV. Free, no API key, stable CSV endpoint, terms permit personal non-commercial use (recorded honestly in `contracts/stooq.yaml`). Alternatives considered: Tiingo (needs key, rate-limited), Alpha Vantage (25 req/day free tier — unusable for a 500-name universe), yfinance (legally murky permitted-use). Revisit only if Stage 1 reconciliation shows quality gaps.

## 2026-07-17 — Theme sourcing

Starter theme + candidates are drawn from the human's `Agents_Economics` research engine (`config/themes.yaml`, `obsidian-brain/Economics/hypotheses|concepts|research`) — human-directed. Stage 0 theme documents are *inputs* to the experiment; every factual claim in them will be re-derived with provenance by the Stage 1 evidence tooling before it can support a decision.

## 2026-07-17 — Stage 1 decisions

**Compose promotion.** `compose.scratch.yaml` → stable `compose.yaml` (named volume `pgdata`, localhost:55432, healthcheck); throwaway test DBs use the `compose.test.yaml` override (tmpfs, port 55433). Localhost-bound → no ufw rule needed.

**Price source: Stooq → Tiingo.** Stooq's CSV endpoint now serves a JavaScript proof-of-work anti-bot challenge to non-browser clients — the plain-HTTP contract assumption failed in practice, and bypassing bot protection is off the table. **Human-confirmed replacement: Tiingo free tier** (`contracts/tiingo.yaml`; `contracts/stooq.yaml` removed, history in git). Tiingo is strictly better on data semantics (explicit raw+adjusted series, splitFactor/divCash fields) ; known scaling cost: the 500-name Stage 3 universe needs the paid tier. `TIINGO_API_KEY` lives in `.env`.

**Transcripts deferred.** EDGAR carries no earnings-call transcripts (third-party/paywalled). Management-emphasis metrics are measured from filings text only (MD&A, 8-K/earnings releases) for the slice. Revisit if emphasis signals prove too sparse.

**FRED key reuse.** `FRED_API_KEY` copied from `Agents_Economics/.env` into this project's `.env` (human-confirmed). The key is passed at request time only and scrubbed from stored `query_params` (`stored_params` mechanism in `data/adapters/core.py`).

**Agents_Economics relationship (human-discussed).** The desk does NOT absorb Agents_Economics now. Economics remains the upstream hypothesis generator; promotions into the desk are tracked via `research_genealogy`. **Absorption trigger:** after the desk passes Gate B (and ideally Gate C), Economics' nightly audit duties can migrate into the desk's milestone-monitoring service (Stage 3); until then the codebases stay separate so untyped narrative assertions never enter the provenance system.

**FRED series for the grid-constraint slice:** `IPUTIL` (Industrial Production: Utilities), `CAPUTLG2211S` (Capacity Utilization: Electric Power Generation/Transmission/Distribution), `INDPRO` (Industrial Production Index). Recorded in `config/experiment.yaml` and the theme doc.

**No price_bars table.** Price observations are derived-on-read from immutably cached Tiingo JSON (`parse_bars`), not stored as rows — avoids a second source of truth for prices in Stage 1. Add a real table deliberately (migration 002 + this log) if Stage 2 analytics need indexed bars.

## 2026-07-17 — Stage 1 slice run 1 lessons

- **Unit-blind figure ratios are a real failure mode.** Run 1 produced GEV `revenue_share = 77.66` (segment revenue extracted in millions, total revenue in billions). Fix: ratios now require identical `figure_unit` on both figures plus plausibility bounds (share ≤ 1, backlog ≤ 20 years); violations raise a classified `INCONSISTENT` data_incident and skip the metric. The extractor prompt pins monetary figures to `USD_millions`. The reviewer had caught the inflation qualitatively — the deterministic layer now catches it structurally.
- **The eligibility gate visibly worked**: FRED vintages ingested at ~13:07 UTC carried `first_eligible_decision_time` = same-day 13:30 UTC session open; the 13:26 UTC run correctly saw zero macro points. Not a bug — the anti-look-ahead guarantee behaving exactly as specified.
- **Reviewer REJECT is a healthy outcome**: thesis v1 (DRAFT) → v2 (REJECTED) with specific challenges and a do-nothing case; genealogy row logged. The desk's first decision was an evidence-insufficient abstention, which the experiment design explicitly rewards.

## Deferred ideas (noted, not built)

- None yet. (Anything a schema tempts us to add beyond spec §3 gets listed here instead of added.)
