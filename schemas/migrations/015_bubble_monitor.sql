-- 015: AI-bubble exit monitor (DECISIONS.md 2026-11-01). Operator-only,
-- quarantined from the analyst (invariant 5) — these tables feed the
-- HUMAN's personal de-risking dashboard, never the desk's evidence,
-- baskets, or engine. Raw-SQL operator tables (like edgar_term_registry),
-- deliberately outside TABLE_MODELS.

-- Human link scores (HUMAN provenance; append-only — newest row per link
-- wins; CLEARED withdraws a score without deleting history).
CREATE TABLE IF NOT EXISTS bubble_human_scores (
    id           BIGSERIAL PRIMARY KEY,
    link         TEXT NOT NULL CHECK (link IN ('A','B','C','D','E','F','G','H','I')),
    status       TEXT NOT NULL CHECK (status IN ('GREEN','YELLOW','RED','CLEARED')),
    note         TEXT NOT NULL,
    recorded_on  DATE NOT NULL,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    provenance   TEXT NOT NULL DEFAULT 'HUMAN' CHECK (provenance = 'HUMAN')
);
CREATE INDEX IF NOT EXISTS bubble_human_scores_link_idx
    ON bubble_human_scores (link, recorded_at DESC);

-- Hard reds (plan §3): any one active → stage ≥ 1. Raised and cleared only
-- by the human; active = cleared_on IS NULL. One active row per key.
CREATE TABLE IF NOT EXISTS bubble_hard_reds (
    id           BIGSERIAL PRIMARY KEY,
    key          TEXT NOT NULL,
    note         TEXT NOT NULL,
    raised_on    DATE NOT NULL,
    cleared_on   DATE,
    cleared_note TEXT,
    provenance   TEXT NOT NULL DEFAULT 'HUMAN' CHECK (provenance = 'HUMAN')
);
CREATE UNIQUE INDEX IF NOT EXISTS bubble_hard_reds_active_key
    ON bubble_hard_reds (key) WHERE cleared_on IS NULL;

-- One computed snapshot per session (COMPUTED; upserted — the nightly job
-- is idempotent). Used for stage-change alerts and the stage history.
CREATE TABLE IF NOT EXISTS bubble_runs (
    session      DATE PRIMARY KEY,
    stage        INTEGER NOT NULL CHECK (stage BETWEEN 0 AND 3),
    red_links    TEXT[] NOT NULL,
    payload      JSONB NOT NULL,
    code_version TEXT NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
