-- 006: scout transparency row (2026-07-24).
-- One row per session recording what the nightly scout did — PROPOSED,
-- REJECTED (by the review gate), or ERROR — so the daily report can show
-- the scout ran even on nights when nothing entered the theme queue.
-- Operational metadata like cycle_log: not provenance, never a decision input.
CREATE TABLE IF NOT EXISTS scout_outcomes (
    id           BIGSERIAL PRIMARY KEY,
    session_date DATE NOT NULL UNIQUE,
    outcome      TEXT NOT NULL CHECK (outcome IN ('PROPOSED', 'REJECTED', 'ERROR')),
    slug         TEXT,
    title        TEXT,
    detail       TEXT,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
