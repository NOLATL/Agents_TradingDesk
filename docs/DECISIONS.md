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

## Deferred ideas (noted, not built)

- None yet. (Anything a schema tempts us to add beyond spec §3 gets listed here instead of added.)
