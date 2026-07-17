-- Stage 3 (forward paper workflow) schema deltas — docs/DECISIONS.md 2026-07-17 Stage 3.
-- Idempotent: safe to apply twice.

-- Cycle idempotency claims + crash-resume checkpoints. Authority over
-- OUTCOMES stays in domain rows (theses/orders/simulation_runs); this table
-- only answers "did this period's cycle run, and how far did it get."
CREATE TABLE IF NOT EXISTS cycle_log (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  cycle_type  TEXT NOT NULL CHECK (cycle_type IN ('daily', 'weekly', 'event')),
  period_key  TEXT NOT NULL,   -- session date (daily), ISO week (weekly), trigger key (event)
  trigger     TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('RUNNING', 'COMPLETE', 'FAILED')),
  phase       TEXT,            -- last completed phase, for resume
  started_at  TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  error       TEXT,
  UNIQUE (cycle_type, period_key)
);

-- Engine incidents re-emerge on every genesis replay; upsert them on a
-- natural key instead of duplicating per run.
CREATE UNIQUE INDEX IF NOT EXISTS data_incidents_engine_natural_key
  ON data_incidents (experiment_id, symbol, incident_class, detected_at)
  WHERE source_name = 'engine';
