-- 008: two-stage scout funnel instrumentation (2026-07-29 follow-on).
-- scout_attempts grows two stages: IDEA (the 3-idea slate from funnel
-- stage 1) and IDEA_GATE (the generous local gate's verdicts + winner).
-- Reuses proposal_checks.record_attempt; the constraint just has to admit
-- the new rows. Idempotent: DROP IF EXISTS before ADD.
ALTER TABLE scout_attempts DROP CONSTRAINT IF EXISTS scout_attempts_stage_check;
ALTER TABLE scout_attempts ADD CONSTRAINT scout_attempts_stage_check
    CHECK (stage IN ('PRECHECK', 'REVIEW', 'IDEA', 'IDEA_GATE'));
