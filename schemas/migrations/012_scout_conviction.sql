-- 012: conviction-seeking scout review loop (2026-08-07 scale-up, user
-- decision). The Sonnet gate now assesses conviction ITSELF (never trusting
-- the generator's self-label) and the scout loops up to 6 turns seeking an
-- approved >= medium proposal; approved-low drafts bank as the fallback and
-- publish labeled when the turns exhaust (invariant 9's one-candidate-per-
-- session is preserved — an approved draft is never discarded).
-- scout_outcomes.review_conviction / .fallback: what the reviewer assessed
--   and whether the published candidate was the banked fallback.
-- scout_attempts.review_conviction: per-turn instrument — measures
--   turns-to-medium and fallback rate over time.
-- All JUDGMENT-typed narrative labels; never a decision input.
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS review_conviction TEXT;
ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS fallback BOOLEAN;
ALTER TABLE scout_attempts ADD COLUMN IF NOT EXISTS review_conviction TEXT;
