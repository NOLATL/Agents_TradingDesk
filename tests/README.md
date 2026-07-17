# tests/ — schema round-trip & invariant-structure tests (Stage 0)
Pydantic ↔ SQL parity, enum integrity, NOT NULL on provenance/eligibility columns, NAV taint flag. DDL validated against a scratch Compose Postgres (`docker compose -f compose.scratch.yaml`).
