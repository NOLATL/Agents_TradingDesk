-- Unified theme intake pipeline (consolidation spec 2026-07-22, DECISIONS.md
-- "Economics absorption + universe unfreeze"). Idempotent: safe to apply twice.
--
-- Themes become first-class rows with source provenance and a DB-enforced
-- acceptance cap; universe membership becomes point-in-time (theme_symbols)
-- so the genesis replay stays deterministic as the universe grows; monitoring
-- gains a never-silently-quiet daily status row per active thesis.

-- Thesis gate: reviewer output stops at REVIEWED; only a human flips to
-- ACTIVE (ops/approve.py `thesis accept`). PG18 allows ADD VALUE in a txn.
ALTER TYPE thesis_status ADD VALUE IF NOT EXISTS 'REVIEWED';

DO $$ BEGIN
  CREATE TYPE theme_source AS ENUM ('AGENT', 'ECONOMICS', 'HUMAN');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  -- DEFERRED = rejected, rolls forward to next week's queue (resurface_on).
  -- CLOSED = thread over (thesis rejected / dead / user close) — a fresh
  -- source proposal creates a NEW row; closed themes never auto-requeue.
  CREATE TYPE theme_status AS ENUM ('CANDIDATE', 'ACCEPTED', 'DEFERRED', 'CLOSED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE thesis_daily_verdict AS ENUM ('NO_CHANGE', 'WATCH', 'REVERSAL_SUSPECTED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS themes (
  id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug                  TEXT NOT NULL UNIQUE,     -- doubles as strategy_tag
  title                 TEXT NOT NULL,
  description           TEXT,
  source                theme_source NOT NULL,
  status                theme_status NOT NULL DEFAULT 'CANDIDATE',
  research_config       JSONB NOT NULL,           -- symbols, fred_series, rss_feeds, emphasis_terms
  source_ref            TEXT,                     -- econ note path / genealogy id / 'human'
  backfilled            BOOLEAN NOT NULL DEFAULT FALSE,  -- one-time migration rows exempt from intake caps
  proposed_on           DATE NOT NULL,            -- ET date; key of the 1-econ-candidate/day cap
  proposed_at           TIMESTAMPTZ NOT NULL,
  decided_at            TIMESTAMPTZ,
  accepted_on           DATE,                     -- ET date; key of the 1-accept/day cap
  resurface_on          DATE,                     -- DEFERRED themes re-enter the queue here
  rolled_forward_count  INTEGER NOT NULL DEFAULT 0,
  closed_reason         TEXT,                     -- thesis_rejected | dead | user_closed
  created_at            TIMESTAMPTZ NOT NULL
);

-- HARD CAP (spec invariant 1): one accepted theme per day, across ALL sources.
CREATE UNIQUE INDEX IF NOT EXISTS one_theme_accept_per_day
  ON themes (accepted_on) WHERE accepted_on IS NOT NULL;

-- Economics scout self-limit: at most one ECONOMICS-sourced candidate per day
-- (backfilled migration rows exempt; the accept cap above still applies to them).
CREATE UNIQUE INDEX IF NOT EXISTS one_econ_candidate_per_day
  ON themes (proposed_on) WHERE source = 'ECONOMICS' AND NOT backfilled;

-- Point-in-time universe membership: a symbol is in-universe from its
-- activation session onward. Pre-activation benchmark history never restates.
CREATE TABLE IF NOT EXISTS theme_symbols (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  theme_id            BIGINT NOT NULL REFERENCES themes(id),
  symbol              TEXT NOT NULL,
  activation_session  DATE NOT NULL,
  created_at          TIMESTAMPTZ NOT NULL,
  UNIQUE (theme_id, symbol)
);

-- Spec invariant 3: monitoring never silently goes quiet. One row per ACTIVE
-- thesis per session, always — NO_CHANGE is a written verdict, not an absence.
CREATE TABLE IF NOT EXISTS thesis_daily_status (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  thesis_id     BIGINT NOT NULL REFERENCES theses(id),
  session_date  DATE NOT NULL,
  verdict       thesis_daily_verdict NOT NULL,
  summary       TEXT NOT NULL,
  post_trade    BOOLEAN NOT NULL,   -- open position existed at check time
  created_at    TIMESTAMPTZ NOT NULL,
  UNIQUE (thesis_id, session_date)
);

ALTER TABLE theses ADD COLUMN IF NOT EXISTS theme_id BIGINT REFERENCES themes(id);

-- ---------------------------------------------------------------------------
-- Backfill: the three config/experiment.yaml stage3 themes become rows.
-- All 15 symbols activate at genesis (2026-07-20) regardless of theme status:
-- the frozen momentum_universe contained all 15, so activating them at genesis
-- is what keeps the replayed benchmark history bit-identical (universe
-- membership is deliberately decoupled from acceptance for this backfill only).
-- ---------------------------------------------------------------------------

INSERT INTO themes (slug, title, source, status, research_config, source_ref,
                    backfilled, proposed_on, proposed_at, decided_at, accepted_on, created_at)
VALUES
  ('grid-constraint', 'Grid constraint: transmission & distribution equipment crunch',
   'HUMAN', 'ACCEPTED', '{"symbols": ["ETN", "GEV", "HUBB", "PWR", "NVT"]}'::jsonb,
   'config/experiment.yaml stage3.themes (genesis)', TRUE,
   DATE '2026-07-20', TIMESTAMPTZ '2026-07-20 00:00:00-04',
   TIMESTAMPTZ '2026-07-20 00:00:00-04', DATE '2026-07-20',
   TIMESTAMPTZ '2026-07-20 00:00:00-04'),
  ('nuclear-renaissance', 'Nuclear renaissance: firm-power rerate',
   'HUMAN', 'CANDIDATE', '{"symbols": ["CEG", "VST", "ETR", "PEG", "DUK"]}'::jsonb,
   'config/experiment.yaml stage3.themes (genesis)', TRUE,
   DATE '2026-07-20', TIMESTAMPTZ '2026-07-20 00:00:00-04', NULL, NULL,
   TIMESTAMPTZ '2026-07-20 00:00:00-04'),
  ('housing-rental-pivot', 'Housing→rental pivot: builders and single-family REITs',
   'HUMAN', 'CANDIDATE', '{"symbols": ["DHI", "LEN", "PHM", "INVH", "AMH"]}'::jsonb,
   'config/experiment.yaml stage3.themes (genesis)', TRUE,
   DATE '2026-07-20', TIMESTAMPTZ '2026-07-20 00:00:00-04', NULL, NULL,
   TIMESTAMPTZ '2026-07-20 00:00:00-04')
ON CONFLICT (slug) DO NOTHING;

INSERT INTO theme_symbols (theme_id, symbol, activation_session, created_at)
SELECT t.id, s.symbol, DATE '2026-07-20', TIMESTAMPTZ '2026-07-20 00:00:00-04'
FROM themes t
JOIN LATERAL jsonb_array_elements_text(t.research_config -> 'symbols') AS s(symbol) ON TRUE
WHERE t.backfilled
ON CONFLICT (theme_id, symbol) DO NOTHING;

-- Link existing theses to their theme rows (strategy_tag = slug by convention).
UPDATE theses
SET theme_id = t.id
FROM themes t
WHERE theses.theme_id IS NULL AND theses.strategy_tag = t.slug;
