"""Apply ordered SQL migrations from schemas/migrations/. Idempotent:

applied migrations are recorded in schema_migrations and skipped on re-run
(and every migration file is itself written to be safe to apply twice).

    uv run python -m data.migrate
"""

from pathlib import Path

from data.db import connect

MIGRATIONS_DIR = Path(__file__).parent.parent / "schemas" / "migrations"


def migrate(conn) -> list[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    applied = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
    ran = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name in applied:
            continue
        conn.execute(path.read_text())
        conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
        ran.append(path.name)
    conn.commit()
    return ran


if __name__ == "__main__":
    with connect() as conn:
        ran = migrate(conn)
    print(f"applied: {ran or 'nothing (up to date)'}")
