-- 010: cross-vendor referee (2026-07-30, DECISIONS.md "Three-vendor
-- architecture" Phase A). The scan's Tiingo-vs-Alpaca adjusted-close
-- comparison raises classified VENDOR_MISMATCH incidents (Gate A: never
-- generic). Idempotent; the new value is not used in this transaction.
ALTER TYPE incident_class ADD VALUE IF NOT EXISTS 'VENDOR_MISMATCH';
