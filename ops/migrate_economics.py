"""One-shot: migrate in-flight Agents_Economics vault notes into the unified
theme intake queue (consolidation 2026-07-22). Idempotent — reruns skip
already-migrated notes.

Concepts and watching-stage notes become CANDIDATE themes; hypothesis/bet
notes become CANDIDATE themes flagged [in-flight bet] so the human can
prioritize them. All rows are backfilled=TRUE (exempt from the 1-candidate/
day intake index) — the one-accepted-theme-per-day cap still applies at
accept time; the backlog trickles through it by design (user decision).

Each migrated note gets `migrated_to:` appended to its frontmatter and the
Economics/ vault folder is left frozen as archive.

    uv run python -m ops.migrate_economics [--dry-run]
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from data import repository as repo
from data.db import connect
from ops.approve import today_et
from schemas.enums import ThemeSource, ThemeStatus
from schemas.models import Theme

ECON_VAULT = Path("/home/jared/obsidian-brain/Economics")


def read_note(path: Path) -> tuple[dict, str]:
    text = path.read_text()
    if not text.startswith("---"):
        return {}, text
    try:
        _, fm, body = text.split("---", 2)
        return yaml.safe_load(fm) or {}, body
    except (ValueError, yaml.YAMLError):
        return {}, text


def mark_migrated(path: Path, slug: str) -> None:
    text = path.read_text()
    _, fm, body = text.split("---", 2)
    path.write_text(f"---{fm}migrated_to: trading-desk/themes/{slug}\n---{body}")


def migrate(conn, dry_run: bool = False) -> list[str]:
    migrated = []
    sources = [
        (ECON_VAULT / "concepts", False),
        (ECON_VAULT / "hypotheses", True),
    ]
    for folder, in_flight in sources:
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.md")):
            meta, body = read_note(path)
            if meta.get("migrated_to"):
                continue
            slug = path.stem.lower()
            if conn.execute(
                "SELECT 1 FROM themes WHERE slug = %s", (slug,)
            ).fetchone():
                continue
            tickers = [str(t).upper() for t in meta.get("tickers", []) if t]
            title = meta.get("title") or meta.get("thesis") or slug
            status = meta.get("status", "?")
            flag = "[in-flight bet] " if in_flight and status in ("hypothesis", "bet") else ""
            description = (
                f"{flag}Migrated from Agents_Economics ({status}). "
                f"{str(meta.get('thesis', ''))[:400]}\n\n{body.strip()[:1500]}"
            )
            if dry_run:
                print(f"would migrate {path.name} -> {slug} ({flag or 'candidate'})")
                migrated.append(slug)
                continue
            now = datetime.now(timezone.utc)
            # backfilled=True at insert: exempt from the 1-econ-candidate/day
            # index (which would otherwise reject the second row); the ACCEPT
            # cap is untouched — the backlog trickles through it.
            repo.insert(conn, Theme(
                slug=slug, title=str(title)[:200], description=description,
                source=ThemeSource.ECONOMICS, status=ThemeStatus.CANDIDATE,
                research_config={"symbols": tickers},
                source_ref=str(path), backfilled=True,
                proposed_on=today_et(), queued_on=today_et(),
                proposed_at=now, created_at=now,
            ))
            conn.commit()
            mark_migrated(path, slug)
            migrated.append(slug)
            print(f"migrated {path.name} -> CANDIDATE {slug}")
    return migrated


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    with connect() as conn:
        slugs = migrate(conn, dry_run=dry)
    print(f"{'would migrate' if dry else 'migrated'} {len(slugs)} theme(s)")
