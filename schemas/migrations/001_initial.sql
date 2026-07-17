-- Trading-desk data model DDL (spec v5 §3). Twin of schemas/models.py —
-- tests/test_schema_roundtrip.py asserts column↔field parity and enum equality.
-- Postgres 18. Idempotent: safe to apply twice (a requirement, not a nicety).

BEGIN;

-- ---------------------------------------------------------------------------
-- Enum types (mirrors schemas/enums.py exactly)
-- ---------------------------------------------------------------------------

DO $$ BEGIN
  CREATE TYPE provenance_type AS ENUM ('SOURCE_FACT', 'COMPUTED', 'JUDGMENT', 'HUMAN');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  -- The four exit types (§5). Trade/thesis/forecast semantics:
  --   PRICE_STOP:     close position | thesis stays active | forecasts keep scoring
  --   CAUSAL_BREAK:   liquidate basket | thesis invalidated | resolve per criterion
  --   VALUATION_EXIT: close at profit | thesis stays valid  | forecasts keep scoring
  --   TIME_STOP:      close position | thesis closed        | mark UNRESOLVED
  CREATE TYPE exit_type AS ENUM ('PRICE_STOP', 'CAUSAL_BREAK', 'VALUATION_EXIT', 'TIME_STOP');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE forecast_outcome AS ENUM ('TRUE', 'FALSE', 'UNRESOLVED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  -- Note: PRICE_STOP / VALUATION_EXIT deliberately absent — trade exits never resolve forecasts.
  CREATE TYPE resolution_cause AS ENUM ('MILESTONE', 'TIME_STOP', 'CAUSAL_BREAK', 'MANUAL');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE thesis_status AS ENUM ('DRAFT', 'ACTIVE', 'INVALIDATED', 'CLOSED', 'REJECTED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE order_status AS ENUM ('PROPOSED', 'APPROVED', 'REJECTED', 'FILLED', 'CANCELLED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE order_side AS ENUM ('BUY', 'SELL');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE milestone_status AS ENUM ('PENDING', 'CONFIRMED', 'FALSIFIED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE agent_role AS ENUM
    ('EVIDENCE_COLLECTOR', 'RESEARCH_ANALYST', 'INDEPENDENT_REVIEWER', 'REPORT_ASSEMBLER');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE incident_class AS ENUM
    ('SPLIT', 'DIVIDEND', 'BAD_PRINT', 'SYMBOL_MAP', 'MISSING_BAR', 'CALENDAR',
     'REVISION', 'UNSUPPORTED_CORPORATE_ACTION', 'STALE_DATA', 'INCONSISTENT');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE incident_status AS ENUM ('OPEN', 'RESOLVED_HUMAN', 'FORCED_LIQUIDATION');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ---------------------------------------------------------------------------
-- §3.4 experiments (first: others reference it)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS experiments (
  id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  registry     JSONB NOT NULL,           -- frozen experiment definition incl. pre-declared success criteria
  code_commit  TEXT,
  config_hash  TEXT,
  started_at   TIMESTAMPTZ,
  ended_at     TIMESTAMPTZ,
  created_at   TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- §3.8 immutable research cache (+ §3.1 bitemporal columns)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS documents (
  id                            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  content_hash                  TEXT NOT NULL UNIQUE,
  raw_bytes                     BYTEA NOT NULL,          -- stored once, never mutated
  extracted_text                TEXT,
  retrieval_url                 TEXT NOT NULL,
  query_params                  JSONB,
  source_name                   TEXT NOT NULL,
  license_note                  TEXT,
  is_raw_immutable              BOOLEAN NOT NULL DEFAULT TRUE CHECK (is_raw_immutable),
  event_time                    TIMESTAMPTZ,
  publication_time              TIMESTAMPTZ,
  ingestion_time                TIMESTAMPTZ NOT NULL,
  first_eligible_decision_time  TIMESTAMPTZ NOT NULL,    -- invariant 2: the anti-look-ahead gate
  source_revision_time          TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS extractions (
  id                     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  document_id            BIGINT NOT NULL REFERENCES documents(id),
  document_content_hash  TEXT NOT NULL,   -- extractions cite the stored hash (§3.8)
  claim_text             TEXT NOT NULL,
  citation_span          TEXT NOT NULL,
  model_tag              TEXT NOT NULL,
  prompt_version         TEXT NOT NULL,
  created_at             TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- §3.2 provenance
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS assertions (
  id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  type            provenance_type NOT NULL,  -- invariant 1: no untyped assertions
  value           JSONB NOT NULL,
  source_doc_id   BIGINT REFERENCES documents(id),
  citation_span   TEXT,
  function_name   TEXT,
  code_version    TEXT,
  model_tag       TEXT,
  prompt_version  TEXT,
  human_rationale TEXT,
  created_at      TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Theses / forecasts / milestones (§3.3, §5)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS theses (
  id                        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id             BIGINT NOT NULL REFERENCES experiments(id),
  title                     TEXT NOT NULL,
  strategy_tag              TEXT NOT NULL,
  status                    thesis_status NOT NULL,
  version                   INTEGER NOT NULL DEFAULT 1,
  parent_version_id         BIGINT REFERENCES theses(id),
  causal_chain              TEXT NOT NULL,
  exposure_basket_approach  TEXT NOT NULL,   -- §7: measured-exposure justification only
  valuation_check           TEXT NOT NULL,
  -- all four exit types must be defined at creation (§5)
  exit_criteria             JSONB NOT NULL CHECK (
                              exit_criteria ? 'PRICE_STOP' AND exit_criteria ? 'CAUSAL_BREAK'
                              AND exit_criteria ? 'VALUATION_EXIT' AND exit_criteria ? 'TIME_STOP'),
  do_nothing_case           TEXT,
  model_tag                 TEXT NOT NULL,
  prompt_version            TEXT NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS forecasts (
  id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  thesis_id             BIGINT NOT NULL REFERENCES theses(id),
  statement             TEXT NOT NULL,
  probability           DOUBLE PRECISION NOT NULL CHECK (probability >= 0 AND probability <= 1),
  horizon_end           DATE NOT NULL,
  resolution_criterion  TEXT NOT NULL,
  resolved_at           TIMESTAMPTZ,
  outcome               forecast_outcome,
  resolution_cause      resolution_cause,
  brier_score           DOUBLE PRECISION,  -- COMPUTED provenance only
  created_by            agent_role NOT NULL,
  model_tag             TEXT NOT NULL,
  prompt_version        TEXT NOT NULL,
  created_at            TIMESTAMPTZ NOT NULL,
  -- resolution comes with a cause, and vice versa (invariant 7 hygiene)
  CHECK ((outcome IS NULL) = (resolution_cause IS NULL)),
  CHECK ((outcome IS NULL) = (resolved_at IS NULL))
);

CREATE TABLE IF NOT EXISTS milestones (
  id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  thesis_id                BIGINT NOT NULL REFERENCES theses(id),
  description              TEXT NOT NULL,
  falsification_criterion  TEXT NOT NULL,
  status                   milestone_status NOT NULL,
  status_changed_at        TIMESTAMPTZ,
  created_at               TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Orders / trades / lots / positions
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS orders (
  id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id           BIGINT NOT NULL REFERENCES experiments(id),
  thesis_id               BIGINT NOT NULL REFERENCES theses(id),
  symbol                  TEXT NOT NULL,
  side                    order_side NOT NULL,
  status                  order_status NOT NULL,
  strategy_tag            TEXT NOT NULL,
  proposed_qty            NUMERIC NOT NULL,        -- the agent-side proposal, kept verbatim
  approved_qty            NUMERIC,                 -- human decision, logged separately (invariant 6)
  human_rationale         TEXT,
  exit_type               exit_type,
  decision_packet_doc_id  BIGINT REFERENCES documents(id),
  proposed_at             TIMESTAMPTZ NOT NULL,
  decided_at              TIMESTAMPTZ,
  created_at              TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
  id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  order_id          BIGINT NOT NULL REFERENCES orders(id),
  symbol            TEXT NOT NULL,
  side              order_side NOT NULL,
  qty               NUMERIC NOT NULL,
  fill_price        NUMERIC NOT NULL,
  slippage_applied  NUMERIC NOT NULL,
  strategy_tag      TEXT NOT NULL,
  exit_type         exit_type,
  executed_at       TIMESTAMPTZ NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL
);

-- Trades are append-only: the ledger is immutable (§6).
CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION '% rows are immutable (append-only ledger)', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;

DO $$ BEGIN
  CREATE TRIGGER trades_immutable BEFORE UPDATE OR DELETE ON trades
    FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS tax_lots (
  id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  opening_trade_id      BIGINT NOT NULL REFERENCES trades(id),
  closing_trade_id      BIGINT REFERENCES trades(id),
  symbol                TEXT NOT NULL,
  qty                   NUMERIC NOT NULL,
  cost_basis            NUMERIC NOT NULL,
  acquisition_date      DATE NOT NULL,
  closed_at             TIMESTAMPTZ,
  realized_pnl          NUMERIC,
  holding_period_days   INTEGER,
  wash_sale_candidate   BOOLEAN NOT NULL DEFAULT FALSE  -- candidate only; not tax-authoritative
);

CREATE TABLE IF NOT EXISTS positions (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id  BIGINT NOT NULL REFERENCES experiments(id),
  symbol         TEXT NOT NULL,
  qty            NUMERIC NOT NULL,
  avg_cost       NUMERIC NOT NULL,
  market_value   NUMERIC,
  frozen         BOOLEAN NOT NULL DEFAULT FALSE,  -- §6 quarantine freeze-and-mark
  frozen_mark    NUMERIC,
  as_of          TIMESTAMPTZ NOT NULL,
  CHECK (NOT frozen OR frozen_mark IS NOT NULL)   -- a frozen position always carries its mark
);

-- ---------------------------------------------------------------------------
-- NAV series with the §6 taint flag
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS daily_nav (
  id                           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id                BIGINT NOT NULL REFERENCES experiments(id),
  nav_date                     DATE NOT NULL,
  nav                          NUMERIC NOT NULL,
  cash                         NUMERIC NOT NULL,
  positions_value              NUMERIC NOT NULL,
  contains_forced_resolution   BOOLEAN NOT NULL DEFAULT FALSE,  -- §6: no perf claim on a fabricated fill
  UNIQUE (experiment_id, nav_date)
);

CREATE TABLE IF NOT EXISTS benchmarks_nav (
  id                           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id                BIGINT NOT NULL REFERENCES experiments(id),
  benchmark_name               TEXT NOT NULL,
  nav_date                     DATE NOT NULL,
  nav                          NUMERIC NOT NULL,
  contains_forced_resolution   BOOLEAN NOT NULL DEFAULT FALSE,
  UNIQUE (experiment_id, benchmark_name, nav_date)
);

-- ---------------------------------------------------------------------------
-- Lessons / agent runs / incidents / genealogy
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lessons (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  thesis_id     BIGINT NOT NULL REFERENCES theses(id),
  dimension     TEXT NOT NULL,  -- §9 post-mortem axis (thesis vs trade correctness scored separately)
  lesson_text   TEXT NOT NULL,
  strategy_tag  TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id   BIGINT NOT NULL REFERENCES experiments(id),
  role            agent_role NOT NULL,
  model_tag       TEXT NOT NULL,      -- pinned per experiment; logged on every run
  prompt_version  TEXT NOT NULL,
  input_hash      TEXT NOT NULL,
  output_hash     TEXT,
  started_at      TIMESTAMPTZ NOT NULL,
  finished_at     TIMESTAMPTZ,
  success         BOOLEAN,
  error           TEXT
);

CREATE TABLE IF NOT EXISTS data_incidents (
  id                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  incident_class           incident_class NOT NULL,  -- classified, never generic (Gate A)
  status                   incident_status NOT NULL,
  source_name              TEXT NOT NULL,
  symbol                   TEXT,
  description              TEXT NOT NULL,
  detected_at              TIMESTAMPTZ NOT NULL,
  resolved_at              TIMESTAMPTZ,
  resolution_assertion_id  BIGINT REFERENCES assertions(id)  -- HUMAN-provenance adjustment
);

CREATE TABLE IF NOT EXISTS research_genealogy (
  id                                       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id                            BIGINT NOT NULL REFERENCES experiments(id),
  hypothesis_title                         TEXT NOT NULL,
  thesis_id                                BIGINT REFERENCES theses(id),
  rejected                                 BOOLEAN NOT NULL,
  rejection_reason                         TEXT,
  variant_count                            INTEGER NOT NULL DEFAULT 1,
  evidence_added_after_price_observation   BOOLEAN NOT NULL,  -- "didn't check" is not an answer
  created_at                               TIMESTAMPTZ NOT NULL
);

COMMIT;
