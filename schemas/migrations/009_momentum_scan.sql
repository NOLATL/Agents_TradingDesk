-- 009: momentum scan (2026-07-29, DECISIONS.md "Momentum scan").
-- Operational reference data like symbol_directory: sp500_constituents is
-- derive-on-read from the newest wikipedia_sp500 document — never a decision
-- input, never point-in-time constituent history (forward-only snapshots).
-- Idempotent: safe to apply twice.

CREATE TABLE IF NOT EXISTS sp500_constituents (
  ticker            TEXT PRIMARY KEY,
  security_name     TEXT,
  gics_sector       TEXT NOT NULL,
  gics_sub_industry TEXT,
  cik               TEXT,
  date_added        DATE,
  snapshot_doc_hash TEXT NOT NULL,     -- provenance: documents.content_hash
  refreshed_at      TIMESTAMPTZ NOT NULL
);

-- The operator-only momentum scan claims its own cycle rows ('scan').
ALTER TABLE cycle_log DROP CONSTRAINT IF EXISTS cycle_log_cycle_type_check;
ALTER TABLE cycle_log ADD CONSTRAINT cycle_log_cycle_type_check
    CHECK (cycle_type IN ('daily', 'weekly', 'event', 'scan'));
