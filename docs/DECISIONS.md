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

## 2026-07-17 — Stage 2 decisions (human-confirmed at kickoff)

**Backups: R2/restic → Obsidian sync + Google Drive/rclone.** The original plan (restic → Cloudflare R2) is replaced per STAGE_2_KICKOFF: the Obsidian vault (packets, notes) is offsite-replicated by Obsidian sync; the database gets nightly `pg_dump | gzip` → local rolling dir → `rclone copy` of the sealed dump to Google Drive (API copy of a finished file, never a live-sync of the data dir). Git → private GitHub repo (human-confirmed; offsite code mirror trumps the general GitHub-migration direction for this purpose). CLAUDE.md environment section updated accordingly. Optimal backup architecture (retention, second provider, encryption-at-rest, frequency) is an explicit **deferred decision** re-opened at the pre-live gate.

**`price_bars` table: build it (migration 002).** Stage 2 is the trigger Stage 1 anticipated. Read-through cache only: every row carries `source_doc_hash` referencing the immutable Tiingo document; the rebuild rule is deterministic (per symbol, latest-ingested document wins wholesale, parsed by the same `parse_bars` function the adapter uses); a test proves `TRUNCATE` + rebuild is bit-identical. Never a second source of truth.

**Forced liquidation: `OrderStatus.FORCED_LIQUIDATION` (narrow invariant-6 exception).** The §6 quarantine timeout fallback force-liquidates without human approval by the spec's own deterministic rule. Represented as a system-generated Order in status FORCED_LIQUIDATION referencing the DataIncident (`decision_packet_doc_id` unused; `human_rationale` NULL). This is the only code path that creates an order without the human gate; the compensating control is the `contains_forced_resolution` taint flag on every affected NAV row from the forced fill onward (**taint-forward rule**).

**Ledger conventions.** Lot selection: **FIFO**; a partial close splits the lot (closed portion becomes its own row sharing `opening_trade_id`). Wash-sale *candidate* = repurchase within ±30 calendar days of a realized-loss close. Dividends credit cash on **ex-date** (Tiingo reports `divCash` on ex-date; pay-date modeling deferred). Quarantine window `quarantine_resolution_window_days` is counted in **trading sessions**, not calendar days, so timeouts land deterministically on a session.

**Engine determinism architecture.** `simulate()` is a pure function (no DB/clock/randomness inside `analytics/engine/`; a purity lint test enforces it); replay inputs are an explicit `ReplayManifest` (stored in `simulation_runs`); human quarantine resolutions are HUMAN-provenance assertions and therefore replay *inputs*. Sizing (floats/sklearn permitted) runs at proposal time, upstream of the replay boundary. `equal_weight_sp500_proxy` control uses the RSP ETF as its proxy series.

## 2026-07-17 — Stage 2 build notes

- **Backup wiring:** `ops/backup.sh` (nightly `pg_dump | gzip` → `~/backups/trading-desk/`, 14-day rolling, then `rclone copy` of the sealed file to `gdrive:Backups/Agents_TradingDesk/database`), driven by a **user systemd timer** (`ops/trading-desk-backup.{service,timer}`, 02:30 nightly, `Persistent=true`, linger enabled). `ops/restore_test.sh` restores the newest dump into an isolated throwaway Compose project (`-p tradingdesk-restore`, tmpfs, port 55433) and asserts schema/row sanity — **passed 2026-07-17** (20 tables, 5,893 price_bars, 145 typed assertions). rclone + gh installed to `~/.local/bin` (no sudo).
- **OPEN (human auth pending):** the interactive OAuth steps for the `gdrive` rclone remote and `gh auth login` were deferred by the human at Gate B close. Until then the offsite Drive leg exits 3 ("remote not configured" — loud, not silent) and no GitHub remote exists. Commands are recorded in STATUS.md; everything downstream is wired.
- **Restore-test isolation lesson:** the first restore-test run reused the default Compose project name and recreated the dev DB container (the named volume survived; no data lost). Fixed: the script uses `-p tradingdesk-restore` so backup drills can never touch the dev database.
- **Benchmark symbols:** SPY + RSP ingested via the Tiingo adapter alongside the slice symbols; all four controls run forward on real data (134 sessions) with bit-identical replay.
- **Slippage config:** conservative starter tiers — 10 bps default, 20 bps ≥ 0.40 annualized vol, 35 bps ≥ 0.60. Calibration against observed opens is Stage 3 work.

## 2026-07-17 — Stage 3 decisions (human-confirmed at kickoff)

**Alerts: ntfy.sh.** Private generated topic in `.env` (`NTFY_TOPIC`), never in git or LLM context beyond the local process. Self-hostable on the tailnet later with only a base_url config change.

**Update (same day, human-requested): ntfy is now self-hosted.** Container via `ops/compose.ntfy.yaml` (project `ntfy`, restart unless-stopped, localhost:2586) exposed tailnet-only as `https://framework.taila4cc29.ts.net:8444` via `tailscale serve` (443/8443 were already in use by other services). `NTFY_UPSTREAM_BASE_URL=https://ntfy.sh` keeps iOS instant push working — only a content-free poll signal transits ntfy.sh; alert content stays on the Framework. Android delivery is fully local. Reachability is gated by tailnet membership; topic name remains the second factor.

**Universe frozen at genesis (2026-07-20): 15 names / 3 themes** — grid ETN GEV HUBB PWR NVT · nuclear CEG VST ETR PEG DUK · housing DHI LEN PHM INVH AMH; `momentum_top_n=10`. Frozen because the universe sits inside `EngineConfig` → config hash → benchmark history; widening after genesis = new experiment id. Research starts grid-only; candidate themes may enter *research* rotation later (config/data, not engine change).

**Offsite backups: RENEWED DEFERRAL (human sign-off at Stage 3 kickoff).** The rclone/Drive and GitHub auth steps remain open through Stage 3; local nightly dumps + passing restore test are the operative backup. Recorded explicitly against Gate C's backup checkbox per the kickoff's deferral clause. Auth commands remain in STATUS.md.

**Live-ledger architecture: genesis replay + prefix verification + incremental persist.** Every run re-simulates EXP-001 from genesis; persisted trades must be a prefix of the replay (compared on seq/symbol/side/qty/price/slippage/tag/session/exit_type/forced); only suffix trades append; previously-persisted lots are UPDATED when later sells close them; NAV/benchmarks/snapshots upsert. Prefix breach → halt + incident + alert; resolution is a HUMAN **bar_pin** assertion (pinned old document overlays sessions ≤ through_session via `bar_pin_assertion_ids` on the manifest). **Fills are historical facts; never restated.** Upstream guards: restatement diff on raw fields before adopting a new Tiingo doc (adj_* free to rescale — dividends legitimately rescale them), completeness gate before replay, Postgres advisory lock around ingest→persist.

**Slippage calibration is report-only during EXP-001.** Tier bps enter historical fills under genesis replay; changing them mid-run rewrites history. Modeled-vs-observed comparison runs as a read-only report; an actual tier change happens only at an experiment boundary.

**CAUSAL_BREAK is staged.** The milestone monitor's LLM only *reads* whether a falsification criterion occurred; human approval of the proposed liquidation is the confirmation event that flips the thesis to INVALIDATED and resolves forecasts (`forecast_resolution_effect`). A rejected read reverts the milestone with rationale. Protects calibration from LLM misreads; quarantine timeout remains the only unapproved order path.

**Schedule:** daily monitor 23:00 ET (post-close, Tiingo composite final; backup timer 02:30 follows it), weekly research Sat 09:00 ET, human approval over the weekend, fills Monday open. Genesis Monday 2026-07-20. Quarantine window remains 5 trading sessions.

## 2026-07-22 — Consolidation: unified theme pipeline + economics absorption (human-confirmed)

The desk and Agents_Economics were consolidated into one pipeline per the
human's consolidation spec. Four decisions the human confirmed explicitly:
full absorption (not loose coupling), drop the universe freeze, decisions via
an extended `ops/approve.py`, and migrate-then-retire the economics outputs.

**Themes are first-class rows (migration 004).** `themes` /`theme_symbols` /
`thesis_daily_status` tables + `theses.theme_id`; `slug` doubles as
`strategy_tag`. Three sources (`AGENT` scout, `ECONOMICS` scout, `HUMAN`)
converge on ONE intake queue with a **DB-enforced hard cap of one accepted
theme per calendar day across all sources** (partial unique index on
`accepted_on`). **Reject is terminal** (theme CLOSED, `user_rejected`);
**UNDECIDED candidates are what roll forward** — the nightly queue roll
(migration 005 `queued_on`; `roll_theme_queue`) defers candidates the human
passed over and resurfaces them Monday, so deciding nothing never kills a
theme and only an explicit reject does. *(Corrected same day at the human's
clarification — the first cut had reject itself deferring.)* A rejected
thesis (or `dead`) likewise CLOSEs the thread; re-entry is only a fresh
proposal.

**Universe freeze RETIRED (supersedes 2026-07-17 "frozen at genesis").**
Membership is now dated, not frozen: `theme_symbols.activation_session` +
`universe_activations`/`basket_activations` in `EngineConfig`
(`data/universe.py`). A symbol enters the momentum universe/basket only from
its activation session onward, so pre-activation benchmark history never
restates — Gate B determinism holds without the freeze. EXP-001 continues
(human choice); benchmark comparability is scoped to "point-in-time expanding
universe". Backfill: all 15 genesis symbols activate 2026-07-20. The cutover
also fixed the same-basket control bug (it held all 15 names instead of the
5-name active-theme basket; the 2 pre-trade sessions of same_basket NAV
restate — daily NAV and the other three controls verified bit-identical).

**Reviewer no longer activates.** `ACTIVATE → REVIEWED`; only the human's
`thesis accept` flips ACTIVE (and builds proposals from that thesis's own
cached packet). Reviewer REJECT stays a quality pre-filter — the theme stays
ACCEPTED and retries next Friday. Reviewer also copies milestones to v2 (they
previously stranded on v1, silencing the monitor for any future ACTIVE thesis).

**Weekly cycle moved Sat 09:00 → Fri 23:45 ET; per-theme deep dives.** One
individual deep dive per accepted theme (never batched), each producing a
`reporting/deepdive/` artifact referenced from the briefing.

**Monitoring never silently quiet.** One `thesis_daily_status` row per ACTIVE
thesis per session, always — NO_CHANGE is a written verdict. The absorbed
economics hypothesis audit runs as a causal-chain integrity read
(INTACT/WEAKENING/BROKEN → NO_CHANGE/WATCH/REVERSAL_SUSPECTED) inside
`ops/milestone_monitor.py`. Post-trade REVERSAL escalates urgently and rides
the existing staged CAUSAL_BREAK path. A missing row renders as MONITOR GAP.

**`dead` is exclusively human.** `ops/approve.py dead <thesis_id>` proposes
TIME_STOP unwinds (order gate confirms; forecasts → UNRESOLVED/MANUAL —
invariant 7 intact); no positions → immediate close. The system never
auto-unwinds or auto-retires; TIME_STOP approval is the confirmation event.

**Economics absorbed (supersedes 2026-07-17 "does NOT absorb… after Gate B" —
deliberate human override).** `research/` module: `candidate_scout` (≤1
candidate/day per lens — ECONOMICS macro RSS+EDGAR trends, AGENT desk
filing-language; DB index enforces the econ cap), `theme_brief` (nightly
narrative per ACTIVE-thesis theme → vault `TradingDesk/research/themes/`),
both running in the daily monitor's new `intake` phase (after `report`, so a
research failure never costs the ledger or briefing input). **LLM stack
(human-specified): Qwen generates, claude-sonnet-5 reviews**
(`research/review_gate.py`, max 2 regenerations with critique fed back;
failures publish nothing silently — rejected briefs carry a REVIEW FAILED
banner, rejected proposals never enter the queue and alert). Anthropic spend
logs to `data/llm_usage.jsonl`; ANTHROPIC_API_KEY copied from the econ .env
(same precedent as the FRED key). Research narrative is never an assertion —
Prime Directive and invariant 5 untouched (deep dives still re-derive
everything with provenance).

**Migration + retirement.** `ops/migrate_economics.py` moved 14 in-flight
vault notes (7 concepts, 7 hypotheses/bets flagged `[in-flight bet]`) into
the queue as backfilled CANDIDATEs — they trickle through the daily accept
cap by design (human choice: no bulk exception). `fleet-economics*` timers
disabled and removed from the hermes fleet generator; Agents_Economics repo
archived with a pointer README; `Economics/` vault folder frozen as archive;
econ `market.db` stays read-only (desk has Tiingo/FRED/Postgres). The daily
briefing's Economics section was removed — the desk's daily report (now with
theme queue, thesis decisions, monitoring lines, fills) is the single feed.

## 2026-07-22 — Post-review hardening (three-agent code review of the consolidation)

A parallel review of the three first-live seams (nightly intake, mid-run
universe activation, Friday deep-dive cycle) found one blocker and several
risks; all fixed same night, 229 tests green:

- **EDGAR ingestion cut over to the DB** (the blocker): `active_theme_symbols`
  read the superseded YAML theme list, so an accepted theme's symbols (the 6
  defense names) would never get filings — Friday's dive would have run on an
  empty evidence packet with no error. Now reads `theme_symbols` like Tiingo;
  unmapped tickers skip with a warning instead of KeyError. Defense filings
  backfilled same night (6 per company cached and eligible).
- **Ingest + intake containment**: Tiingo/EDGAR ingest commit per symbol and
  contain per-symbol failures (one bad ticker no longer discards the whole
  night; failures ntfy loudly). The intake phase catches scout/brief crashes
  (Ollama down, schema-invalid output) as high-priority notifications — the
  ledger and report are already safe by then, and a FAILED daily cycle never
  auto-retries, so research failures must not cost the cycle.
- **Friday visibility**: a reviewer-REJECTED dive now still writes its
  deep-dive artifact (banner + reviewer challenges, no accept footer), the
  weekly notify always fires (including "nothing survived review" and no-op
  Fridays), and the cycle re-renders the daily note afterward so Saturday's
  06:15 briefing shows the new REVIEWED theses. Fixed the FAILED-path phase
  overwrite that wiped per-theme resume markers (duplicate dives on rerun).
- **Theme-scoped research config**: the evidence extractor's preference line
  and segment figure are no longer grid-hardcoded (`segment_revenue_theme`,
  legacy name still read); `fred.ingest_all` also pulls accepted themes'
  `research_config.fred_series`. `directed-energy-procurement` got defense
  emphasis terms + FDEFX (verified against the FRED API).
- **Known accepted risks** (not changed): a missing bar for an active universe
  symbol halts the run by design (no deactivation path — raise with the human
  if a symbol goes permanently dark); the reviewer flagged the same-basket
  restatement as a finding, but it is the documented one-time basket-bug fix
  above, not a defect. The `num_ctx` option passed via Ollama's
  OpenAI-compatible endpoint is likely ignored (server default applies) —
  revisit if long contexts truncate.

## 2026-07-23 — Unified mandatory daily scout (human-directed)

- **Two lenses collapsed into one pass**: the ECONOMICS and AGENT scout
  lenses merge into a single nightly `scout(conn)` whose context combines
  the macro RSS flow (168h), the discovery-config EDGAR terms, and the
  slice emphasis terms. New rows are tagged `AGENT` with
  `source_ref=candidate_scout/unified`; `ECONOMICS` remains as historical
  provenance on backfilled rows only, and the `one_econ_candidate_per_day`
  index is now vestigial (harmless, left in place).
- **The proposal is mandatory — zero is no longer a respectable answer**:
  the human wants exactly one candidate theme per weekday (per session) in
  the queue, regardless of how many prior themes were accepted. The scout
  prompt requires one proposal even on quiet days (stating plainly where
  evidence is thin, so the human can reject); `propose: false` is gone from
  the schema. Every no-theme outcome besides an idempotent same-day re-run
  raises (`ReviewRejected` / `ScoutError` on empty or duplicate slug) and
  notifies at high priority — a missing candidate is a visible failure,
  never a quiet outcome. The acceptance slot (invariant 9) is untouched:
  it caps decisions, never proposals.
- **Human proposals never block the scout**: the idempotency gate filters
  `source <> 'HUMAN'`, so a same-day `theme propose` doesn't consume the
  scout's daily slot.
- **Report wording**: the daily note's empty-queue line now explains that
  the report renders before intake, so the night's proposal lands in the
  next morning's briefing (the old "(no candidates today)" read as if
  generation had been skipped).

## Deferred ideas (noted, not built)

- None yet. (Anything a schema tempts us to add beyond spec §3 gets listed here instead of added.)
