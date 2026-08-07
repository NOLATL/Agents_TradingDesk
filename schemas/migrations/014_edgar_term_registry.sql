-- 014: dynamic EDGAR-term registry (2026-08-07 scale-up P4, human-confirmed).
-- A ~monthly local Qwen pass proposes new full-text trend-scan terms from
-- recent news flow; the HUMAN accepts or rejects each via `ops.approve terms`
-- (HUMAN provenance — decided_rationale records why). ACCEPTED terms merge
-- with config/discovery.yaml edgar_terms in the scout context, so the term
-- list evolves without the config going stale. Config-over-hardcoding is
-- preserved: the static baseline stays in discovery.yaml; this table holds
-- only human-approved additions.
CREATE TABLE IF NOT EXISTS edgar_term_registry (
    id                BIGSERIAL PRIMARY KEY,
    term              TEXT NOT NULL UNIQUE,
    status            TEXT NOT NULL DEFAULT 'PENDING'
                      CHECK (status IN ('PENDING', 'ACCEPTED', 'REJECTED')),
    rationale         TEXT,          -- the proposer's case for the term
    proposed_on       DATE NOT NULL,
    decided_on        DATE,
    decided_rationale TEXT           -- the human's reason (HUMAN provenance)
);
