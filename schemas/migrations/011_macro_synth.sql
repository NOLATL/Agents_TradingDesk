-- 011: Saturday macro synthesis (Phase D, DECISIONS.md 2026-08-07). The
-- weekly research/macro note claims its own cycle rows ('macro_synth') —
-- kept out of ops/weekly_cycle.py entirely so Gate C's engine cycle is
-- untouched. Idempotent.
ALTER TABLE cycle_log DROP CONSTRAINT IF EXISTS cycle_log_cycle_type_check;
ALTER TABLE cycle_log ADD CONSTRAINT cycle_log_cycle_type_check
    CHECK (cycle_type IN ('daily', 'weekly', 'event', 'scan', 'macro_synth'));
