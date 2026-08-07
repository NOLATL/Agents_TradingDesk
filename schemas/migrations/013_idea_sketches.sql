-- 013: intraday Qwen watch staging (2026-08-07 scale-up P3, human-confirmed).
-- idea_sketches: operational staging like idea_seeds/scout_outcomes — scored
--   idea sketches accumulated by the local-only intraday watch (07/11/15/19
--   ET, 7 days/week) for the NEXT nightly scout session. Never provenance,
--   never a decision input, never published without passing the nightly
--   Sonnet gate (invariant 13 intact).
-- session_date: the XNYS session whose 23:00 scout consumes the sketch
--   (weekend batches map to Monday). evidence: RESOLVED lines + links —
--   batch-local [Hn] citation IDs do not survive; the nightly scout
--   re-issues each sketch as a fresh [Sn] context item.
-- dedup_key: normalized domain+title slug; UNIQUE(session_date, dedup_key)
--   is the cross-batch dedup.
CREATE TABLE IF NOT EXISTS idea_sketches (
    id            BIGSERIAL PRIMARY KEY,
    session_date  DATE NOT NULL,
    batch_key     TEXT NOT NULL,
    working_title TEXT NOT NULL,
    hypothesis    TEXT NOT NULL,
    domain        TEXT,
    evidence      JSONB NOT NULL,
    gate_rank     INT,
    gate_reason   TEXT,
    dedup_key     TEXT NOT NULL,
    model_tag     TEXT,
    status        TEXT NOT NULL DEFAULT 'PENDING'
                  CHECK (status IN ('PENDING', 'WINNER', 'PASSED_OVER', 'EXPIRED')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_date, dedup_key)
);
CREATE INDEX IF NOT EXISTS idea_sketches_session_status_idx
    ON idea_sketches (session_date, status);

-- The intraday watch claims its batches through cycle_log like every other
-- timer-driven job (idempotent re-runs). Widen the type check (008 pattern).
ALTER TABLE cycle_log DROP CONSTRAINT IF EXISTS cycle_log_cycle_type_check;
ALTER TABLE cycle_log ADD CONSTRAINT cycle_log_cycle_type_check
    CHECK (cycle_type IN ('daily', 'weekly', 'event', 'scan', 'macro_synth',
                          'intraday'));
