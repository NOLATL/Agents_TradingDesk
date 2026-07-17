# schemas/ — the data model (Stage 0)
Single source of schema truth: Pydantic models (`models.py`, `enums.py`) and the matching SQL DDL (`ddl.sql`). Every cross-boundary object imports from here. Schema changes are versioned.
