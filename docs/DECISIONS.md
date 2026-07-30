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

## 2026-07-23 — Deep-dive report to research-report grade (human-directed)

- **The Friday deep-dive renders like a sell-side research report, entirely
  from persisted objects**: `reporting/deepdive.py` now rehydrates the full
  dive from Postgres alone (basket JSON, reviewer audit + executive-summary
  JUDGMENT assertions, both forecast versions for the pre-/post-review
  probability diff, claims/metrics/macro assertions) and renders 13 sections
  ending in a packet-style provenance appendix. No analyst/reviewer prompt or
  schema changed — prompt_version is untouched, so this is the same
  experiment. The independent review (challenges, price-narrative check,
  do-nothing case) now renders on EVERY report, not only on rejection, and
  the quarantined momentum/volatility values appear there labeled
  reviewer-only (human-facing display only; invariant 5 governs analyst
  context and basket construction, which never see them).
- **Rehydration is manifest-first, window-closure fallback**: `run_slice`
  writes a COMPUTED `evidence_packet_manifest` assertion (claim/metric/macro
  assertion ids keyed to the surviving thesis version); pre-manifest dives
  fall back to the run-clock timestamp + first-post-packet-JUDGMENT-id
  window. Missing pieces degrade to loud placeholders, never exceptions —
  a render crash inside `research_pass` would fail the cycle after committed
  LLM work.
- **Repair render closes the crash-window gap**: the weekly `report` phase
  backfills any REVIEWED/REJECTED thesis of the week whose deep-dive file is
  missing (idempotent; file-existence check).
- **Draft renders never touch cycle artifacts**: `ops/render_deepdive.py`
  renders any thesis (SELECT-only) to `reporting/output/drafts/` with a
  DRAFT banner for layout review — never the real deepdive/ dir, vault,
  cycle_log, or documents.

## 2026-07-24 — Scout transparency in the briefing (human-directed)

The 2026-07-23 night exposed two gaps: a review-rejected scout proposal left
no trace in the next briefing (queue just read "empty"), and the rejection
alert itself crashed the run (em dash in an ntfy Title header — headers must
be ASCII; `notify()` now sends metadata as UTF-8 query params and its alert
path never raises). The human's direction: the briefing must confirm the
nightly research ran and say what happened, whatever the outcome.

- **`scout_outcomes` (migration 006)**: one row per session — PROPOSED /
  REJECTED (with the reviewer's critique) / ERROR — upserted by the intake
  phase. Operational metadata like `cycle_log`, not provenance, never a
  decision input.
- **Report renders the latest outcome** in the Theme queue section:
  "Nightly scout: ran and proposed `slug` — Title — but the review agent
  rejected it … Reviewer's reason: >…". A stale row renders with its date.
- **Report re-renders after intake.** The pre-intake render stays as the
  crash-safe floor; the final render carries tonight's scout outcome and any
  new candidate. This also fixes a latent lag: a scout-proposed candidate
  previously took TWO mornings to reach the briefing (report wrote before
  intake, so the briefing embedded yesterday's queue).

## 2026-07-25 — Decision packet demoted to DB-only evidence (human-directed)

An output review found the research-report-grade deep dive (2026-07-23) had
become a strict superset of the decision packet: every packet section renders
inside the dive, and both were written per theme, same Friday, to repo and
vault. The human's direction: delete the packet files, stop producing them,
and make the remaining interdependency obvious. The packet's irreplaceable
role was never the file — `cache_packet` already stored the full content in
an immutable `documents` row (invariant 6 order evidence, invariant 3 replay).

- **Packets are DB-only.** `reporting/packet.py` owns the whole lifecycle:
  `render` → `cache` (sha256-deduped `documents` row, `source_name
  'desk_packet'`, `retrieval_url desk://packets/decision-packet-<theme>-<date>.md`)
  → `latest_doc_id`. No packet file is written anywhere; `packet.write` and
  the vault `packets/` mirror are gone. Legacy `file://` rows still match
  `latest_doc_id`'s LIKE pattern; their dangling paths are harmless — the
  content lives in the row.
- **Caching moved into `run_slice`** (returns `(thesis, packet_doc_id)`),
  closing the crash window between a dive's commit and the packet cache that
  the weekly repair pass previously had to tolerate.
- **The deep dive is the sole per-theme human report.** It cross-links the
  evidence row ("documents #N"); the order gate cites the row id; the new
  `ops.approve packet <doc_id> [--out]` prints the exact cached evidence —
  the invariant-6 viewing path now that there is no file to open.
- **Draft renderer deleted** (`ops/render_deepdive.py` + `output/drafts/`,
  superseding the 2026-07-23 "draft renders" note): its layout-review job
  ended with the deep-dive rewrite, and it was a third code path rendering
  the same report. Recoverable from git (`ae15aa8`) if ever needed.
- Existing packet files were deleted from `reporting/output/` and the vault;
  `documents` rows were untouched (immutable, referenced by orders).

## 2026-07-25 — Deep-dive report v2: FT-style narrative + sell-side layout (human-directed)

The human read the first real W30 dives and rejected the presentation: claim
bullets + verbatim-quote sub-bullets are near-duplicates; the report must
read like an FT long read structured like a sell-side note (recommendation
first, stat block, comparison scoreboard, charts/indicators), with citations
as markdown FOOTNOTES, and carry **≥10 pages of body prose (4,500–10,000
words, tables/charts/footnotes excluded)** — thoroughness over brevity.

- **Narrative belongs to the Report Assembler (role 4)** — the constitution's
  "write reports" role. `reporter.narrate()` generates per-section (lead,
  thesis, macro, one per company, valuation, forecasts, risk, monitoring —
  ~10 local qwen calls per dive) and persists ONE JUDGMENT assertion
  `kind='report_narrative'` keyed to the surviving thesis. The renderer stays
  deterministic and LLM-free (repairs/backfills rehydrate from Postgres).
- **Containment, not trust**: `reporting/narrative_guard.py` mechanically
  validates every section — each number must trace to source objects (exact
  restatement or rounding to FEWER significant digits, via a digit-substring
  tier + a rounding-lattice tier), every `[^A###]` marker must be a real
  assertion id of the dive, tickers must be in the basket, and each section
  must clear its word floor (violations become the retry critique, including
  "expand: deepen the analysis of <uncited ids>"). The assembled whole then
  passes the Sonnet gate (invariant-13 pattern; offline = deterministic
  checks only + loud note; a REJECT regenerates only the named sections).
  Validation is containment of the narrator — the underlying facts carry
  their own provenance.
- **Experiment ruling (human)**: the Report Assembler's new prompt is NOT a
  new experiment condition — its output is JUDGMENT-only presentation and
  never feeds a forecast, basket, or order. Analyst/reviewer prompts are
  untouched. (The deferred-ideas note about *analyst*-authored narrative
  stays deferred for exactly that reason.)
- **Layout**: verdict line + StockRover-style stat table first; exposure
  scoreboard (one symbol×metric matrix with 🟢/🟡/🔴 badges and ▰ bars)
  replaces per-company mini-tables; macro table gains sparklines; Mermaid
  xychart blocks for macro series, exposure scores, and forecast revisions
  (render in Obsidian, degrade to code blocks elsewhere — accepted);
  reviewer verdict/notes as Obsidian callouts; provenance appendix is now
  footnote definitions (`[^A1454]: SOURCE_FACT — doc 938: "…"`), uncited
  evidence in a folded callout. Any missing narrative section falls back to
  the old deterministic rendering with a loud warning — the Friday cycle can
  never fail on narrative problems.
- **Backfill**: `ops/backfill_narrative.py <week>` regenerates narrative for
  past dives from rehydrated evidence and rewrites the reports in place; the
  W30 pair was backfilled on ship day.
- **Ship-day iteration findings** (five backfill rounds, each narrowing the
  failure surface — details in the commit messages `1b416a5`…`8bf88fe`):
  the guard corpus must mirror serialize_context EXACTLY (a field shown to
  the narrator but absent from the corpus rejects legitimate restatements);
  truncation counts as rounding-to-fewer-digits; qwen's compound citations
  are normalized; the Sonnet audit got its own rubric (fail only
  fabrication/mischaracterization/unsupported citation) + structured
  failing-section verdicts. **Quarantined price metrics are citable
  reviewer-domain evidence for the narrator** — they already render in the
  report (see above), the reviewer's prose quotes them, and withholding
  their ids made qwen mis-cite backlog assertions; the prompt bans them as
  ranking justification, so invariant 5 (analyst context + basket
  construction) is untouched. **--repair mode converges monotonically**:
  kept sections stay verbatim, only missing ones are generated and only new
  material audited — full re-rolls lost approved sections to fresh dice.
  Final W30 state: both reports complete, all sections Sonnet-approved,
  4,166 / 4,780 body words.

**bar_pin resolution CLI shipped + first use (2026-07-28).** Stage 3 designed
the vendor-restatement resolution (manifest overlay via `bar_pin_assertion_ids`)
but never built the human entry point. Added `ops.approve pin <symbol>
--rationale` — inserts the HUMAN assertion ({doc_hash_old, doc_hash_new,
through_session=max nav_date}), marks the symbol's OPEN REVISION incidents
RESOLVED_HUMAN, and `restatement_guard` now skips pinning when an assertion
accepts the exact candidate doc (adoption goes forward; replay history stays
on old bars ≤ through_session — fills never restate). Covered by
`test_bar_pin_assertion_unpins_and_resolves`. First use: DUK, where Tiingo
dropped the 2026-06-12 div_cash 0.3516→0.0; ledger was empty so adoption was
materially free (human decision, jared, 2026-07-28). Note: the Gate-C test
suite runs against the dev Postgres — an unresolved live restatement leaks
into `pinned_symbols` and fails the guard tests until resolved.

**Scout anti-hallucination overhaul (2026-07-29).** The nightly scout was
0-for-2 with the review gate since outcome logging began (07-27, 07-28 —
both rejected for fabricated tickers/companies/figures and a misread EDGAR
direction), because the mandatory nightly proposal demanded a 4-paragraph
per-ticker decision document while the context supplied only headline titles
and raw EDGAR hit-count JSON — the specificity gap got filled from model
memory. Shipped (plan reviewed with Jared in-session):
- **Deterministic precheck** (`research/proposal_checks.py`, migration 007):
  evidence items carry citation IDs (`[Hn]`/`[En]`) the proposal must
  reference; EDGAR trends render explicit ROSE/DECLINED/FLAT/NO DATA labels
  (a DECLINED term cited with rising language is flagged); proposed tickers
  are validated against a weekly-refreshed Tiingo `symbol_directory`
  (105k tickers, unmetered zip download, zero curation). Precheck failures
  regenerate locally without spending a Sonnet review attempt; exhaustion
  still reaches the reviewer — never a silent drop. `scout_attempts` records
  every PRECHECK/REVIEW event (the readout instrument for ~10 sessions out).
- **US-only symbol rule (human, 2026-07-29)**: scout tickers must be
  NYSE/NASDAQ/AMEX-listed stocks — no foreign listings, no OTC/ADRs. A
  foreign-only pure-play is dropped and the gap noted, never substituted.
- **Automatic identity enrichment**: the ≤6 proposed tickers get Tiingo meta
  name/description cached into the directory (~6 metered calls/night, mostly
  cache hits) and rendered into the review context as a SYMBOL DIRECTORY
  CHECK block — the reviewer audits company identity against authoritative
  data, not parametric memory (kills the ALTR-class wrong-company failure).
- **Grounding enrichment**: RSS context now carries item summaries (400-char
  cap, stdlib HTML strip), not just titles; recently-REJECTED proposals
  (14d) render into context with critiques so the scout stops re-proposing
  the same family (07-27/07-28 were near-identical rare-earth themes); the
  reviewer's scout context_cap rose 12k→40k chars so it can actually verify
  citations (it was auditing against truncated evidence).
- **Conviction + rubric rebalance (human ruling)**: proposals self-declare
  `conviction: low|medium|high`; the new `SCOUT_REVIEW_SYSTEM` makes factual
  grounding the hard bar but explicitly APPROVES an honest low-conviction
  proposal (thin evidence flagged, every claim grounded) — the human makes
  the accept/reject call at `ops.approve`. Honest-thin and fabricated-thick
  previously both rejected, which pressured the model to fabricate.
  Conviction is recorded on scout_outcomes and rendered in the briefing.
- **Experiment ruling (human, 2026-07-29)**: the scout *proposer* model is
  research-layer config, NOT an engine experiment condition (same class as
  the 2026-07-25 Report Assembler ruling — its output is JUDGMENT-only and
  never feeds a forecast, basket, or order). Model switches for the scout
  are allowed but must be tracked here with tag+digest+date.
  `stage1_slice.llm.scout_model_tag` overrides the scout's model only; the
  four engine roles keep the frozen `qwen3:30b-a3b`. **SWAPPED same day
  (2026-07-29, human direction — "no reason to wait"):** scout now runs
  `qwen3.6:35b` (Qwen3.6-35B-A3B, Apr 2026 — same 3B-active MoE class on
  this hardware, materially better instruction-following and long-tail
  knowledge than the Apr-2025 tag), **digest `07d35212591f`**, 23 GB.
  Smoke-verified on-box: valid structured JSON at num_ctx 40960 with correct
  evidence-ID citations. Attribution note: grounding fixes and the model
  swap ship together at the human's call — the `scout_attempts` PRECHECK
  rows still separate deterministic catches from review-quality effects.

**Two-stage scout idea funnel + native Ollama API (2026-07-29 evening, Jared's
design).** Same-day follow-on to the anti-hallucination overhaul after the
first live approval (`maritime-logistics-volatility`). The remaining ceiling
was evidence depth — proposals were written from 400-char RSS summaries and
EDGAR hit-counts. Shipped:
- **Idea funnel** (`research/idea_funnel.py`, migration 008): Stage 1 — one
  local generation sketches THREE distinct ideas from the summary-level
  context (breadth pressure; three different domains required). Stage 2 — a
  GENEROUS local gate (default KEEP; kills only clear duplicates,
  evidence-free, or price-based ideas; deterministic citation validation
  overrides the judge both ways) ranks survivors by evidence specificity +
  domain freshness. Stage 3 — deterministic deep dive for the winner only:
  full article text (cached feed `content:encoded` bodies first — TWZ/USNI/
  Substacks carry complete articles; link fetch with browser UA + redirects
  as fallback; summary as the floor) excerpted via `chunking.relevant_excerpt`
  (4K chars/article, ≤5 articles, 18K total), plus "companies driving this
  trend" parsed from the ALREADY-CACHED EDGAR full-text responses
  (`display_names` carry company+ticker+CIK — zero new API calls). Stage 4 —
  the existing precheck/refinement/review pipeline unchanged, on the
  enriched context (reviewer context_cap 40K→60K chars). **The funnel is
  pure enrichment: any failure degrades to the single-pass pipeline — the
  mandatory nightly proposal never depends on it.** Adds exactly two local
  generations (~5 min); IDEA/IDEA_GATE rows in `scout_attempts` instrument
  it.
- **Native Ollama API (infra ruling):** `agents/llm.py` moved from the
  OpenAI-compat `/v1/chat/completions` shim to native `/api/chat`. The shim
  silently ignored `options.num_ctx` (server ran 32768 regardless of the
  configured 40960 — confirmed via `ollama ps`); the native endpoint honors
  it (verified: CONTEXT 40960 after the switch) and adds schema-constrained
  decoding via `format`. Layer-1 transport change only — same models, same
  prompts, same prompt_version hashes — NOT an experiment condition (same
  class as the 2026-07-25 Report Assembler ruling).
- **Feed pool widened (human choice):** gcaptain (shipping), mining.com
  (materials), fiercepharma (healthcare), modernfarmer (agriculture — agweb
  403s behind a bot wall). All verified 200 + RSS 2.0 at add time; dead
  feeds parse to zero items.
- **RSS items now carry `link` + uncapped `body`** (summary keeps its
  400-char cap so the rendered scout context is unchanged);
  `fetch_and_cache` gained `follow_redirects`.
- Watch item for the first 3 nightlies: IDEA_GATE kill-rate — if the
  "generous" gate kills >1 idea/night as duplicates, loosen its prompt.
- **Human reject frees the session slot (human decision, jared, 2026-07-29):**
  after rejecting `maritime-logistics-volatility` (thin inferential evidence)
  Jared asked for a fresh run the same night. `_candidate_exists_today` now
  ignores themes with `status=CLOSED AND closed_reason=user_rejected` — the
  gate exists to make re-runs idempotent, not to waste a session on a dead
  candidate. Undecided candidates still block (one live proposal per
  session); invariant 9's acceptance cap is untouched. Note: the session's
  scout_outcome row is upserted by the fresh run; the rejected theme's own
  row + HUMAN assertion remain the durable record.

## Deferred ideas (noted, not built)

- **Analyst-authored narrative report sections** (industry backdrop, per-name
  bull/bear cases, catalysts) — requires extending `AnalystOutput` + the
  analyst system prompt, i.e. a new prompt_version = a new experiment
  condition. Revisit deliberately, not as a report tweak. The 2026-07-25
  Report-Assembler narrative does NOT cover this: role 4 restates verified
  objects; role 2 authoring new analysis remains deferred. (2026-07-23)
