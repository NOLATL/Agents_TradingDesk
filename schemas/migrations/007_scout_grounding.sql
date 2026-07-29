-- 007: scout grounding instruments (2026-07-29, anti-hallucination work order).
-- symbol_directory: Tiingo supported-tickers reference, refreshed weekly by the
--   nightly monitor. Operational reference data like cycle_log — used to
--   precheck scout proposals (ticker existence + US-exchange rule) and to show
--   the reviewer authoritative company identities. Never a decision input.
-- scout_attempts: one row per scout generate attempt, recording which stage
--   (local PRECHECK vs Sonnet REVIEW) passed or failed and why — the
--   measurement instrument for the rejection-reduction readout.
-- scout_outcomes.conviction: the proposal's self-declared evidence strength,
--   surfaced in the morning briefing so the human weighs it at ops.approve.
CREATE TABLE IF NOT EXISTS symbol_directory (
    ticker          TEXT PRIMARY KEY,
    name            TEXT,
    description     TEXT,
    exchange        TEXT,
    asset_type      TEXT,
    price_currency  TEXT,
    list_start      DATE,
    list_end        DATE,
    refreshed_at    TIMESTAMPTZ NOT NULL,
    meta_fetched_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS scout_attempts (
    id            BIGSERIAL PRIMARY KEY,
    session_date  DATE NOT NULL,
    attempt_no    INT NOT NULL,
    stage         TEXT NOT NULL CHECK (stage IN ('PRECHECK', 'REVIEW')),
    passed        BOOLEAN NOT NULL,
    issues        TEXT,
    context_chars INT,
    model_tag     TEXT,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS scout_attempts_session_idx
    ON scout_attempts (session_date);

ALTER TABLE scout_outcomes ADD COLUMN IF NOT EXISTS conviction TEXT;
