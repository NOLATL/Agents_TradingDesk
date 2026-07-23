-- Queue-window correction (human clarification, 2026-07-22 — DECISIONS.md):
-- an explicit reject CLOSES the thread; it is the UNDECIDED candidates that
-- roll forward to Monday. `queued_on` records when a theme last entered the
-- visible queue (at proposal, or at a Monday resurface) so the nightly roll
-- can defer exactly the candidates the human passed over, and never the ones
-- that just arrived. Idempotent: safe to apply twice.

ALTER TABLE themes ADD COLUMN IF NOT EXISTS queued_on DATE;
UPDATE themes SET queued_on = proposed_on WHERE queued_on IS NULL;
ALTER TABLE themes ALTER COLUMN queued_on SET NOT NULL;
