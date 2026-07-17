-- Stage 2 (Gate B) schema deltas — see docs/DECISIONS.md 2026-07-17 Stage 2.
-- Idempotent: safe to apply twice.

-- Narrow invariant-6 exception (spec §6 quarantine timeout fallback):
-- system-generated forced-liquidation orders. PG18 allows ADD VALUE in a txn.
ALTER TYPE order_status ADD VALUE IF NOT EXISTS 'FORCED_LIQUIDATION';

-- Read-through price cache. NEVER a second source of truth: every row carries
-- the hash of the immutable Tiingo document it derives from; rebuild rule is
-- "latest-ingested document per symbol wins wholesale" and a test proves
-- TRUNCATE + rebuild is bit-identical.
CREATE TABLE IF NOT EXISTS price_bars (
  id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  symbol           TEXT NOT NULL,
  session_date     DATE NOT NULL,
  open             NUMERIC NOT NULL,
  high             NUMERIC NOT NULL,
  low              NUMERIC NOT NULL,
  close            NUMERIC NOT NULL,
  volume           BIGINT NOT NULL,
  adj_open         NUMERIC NOT NULL,
  adj_high         NUMERIC NOT NULL,
  adj_low          NUMERIC NOT NULL,
  adj_close        NUMERIC NOT NULL,
  adj_volume       NUMERIC NOT NULL,
  split_factor     NUMERIC NOT NULL DEFAULT 1,
  div_cash         NUMERIC NOT NULL DEFAULT 0,
  source_doc_hash  TEXT NOT NULL REFERENCES documents(content_hash),
  UNIQUE (symbol, session_date)
);

-- Replay manifests + result fingerprints: Gate B evidence as a first-class
-- artifact. Same manifest + same result_hash on re-persist = idempotent no-op;
-- same manifest + DIFFERENT hash = determinism breach (operational breaker).
CREATE TABLE IF NOT EXISTS simulation_runs (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id BIGINT NOT NULL REFERENCES experiments(id),
  manifest      JSONB NOT NULL,
  manifest_hash TEXT NOT NULL UNIQUE,
  result_hash   TEXT NOT NULL,
  code_commit   TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL
);

-- Quarantine incidents are per-experiment state (ingestion incidents may have none).
ALTER TABLE data_incidents ADD COLUMN IF NOT EXISTS experiment_id BIGINT REFERENCES experiments(id);

-- Position rows are per-session snapshots (engine outputs): idempotent writes.
DO $$ BEGIN
  ALTER TABLE positions ADD CONSTRAINT positions_snapshot_unique
    UNIQUE (experiment_id, symbol, as_of);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- NAV rows are also idempotent-write snapshots (daily_nav already UNIQUE).
