-- 011: human idea seeds (2026-07-31, Jared's request).
-- Operator-queued research ideas the nightly scout develops in place of the
-- organic idea funnel (FIFO, oldest first, one per night). The seed becomes
-- the forced "winning idea" block; drafting, precheck, and Sonnet review run
-- unchanged. Operational queue metadata like scout_outcomes: not provenance,
-- never a decision input — the HUMAN assertion for queue/cancel lives in
-- assertions.
CREATE TABLE IF NOT EXISTS idea_seeds (
    id           BIGSERIAL PRIMARY KEY,
    seed_text    TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'PENDING'
                 CHECK (status IN ('PENDING', 'CONSUMED', 'CANCELLED')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_on  DATE,
    outcome      TEXT CHECK (outcome IN ('PROPOSED', 'REJECTED')),
    theme_slug   TEXT,
    detail       TEXT,
    cancelled_at TIMESTAMPTZ
);
-- scout_attempts grows a SEED stage so the instrument distinguishes seeded
-- nights from organic ones (same widening pattern as 008).
ALTER TABLE scout_attempts DROP CONSTRAINT IF EXISTS scout_attempts_stage_check;
ALTER TABLE scout_attempts ADD CONSTRAINT scout_attempts_stage_check
    CHECK (stage IN ('PRECHECK', 'REVIEW', 'IDEA', 'IDEA_GATE', 'SEED'));
