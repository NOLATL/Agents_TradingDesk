"""Draft render of a deep-dive report for layout review — NOT a cycle artifact.

Rehydrates a thesis from Postgres (SELECT-only) and writes the rendered
report to reporting/output/drafts/, never to the real deepdive/ output dir,
the vault, cycle_log, or documents — safe to run any time, including right
before a Friday cycle.

    uv run python -m ops.render_deepdive <thesis_id> [--week 2026-W29] [--out path]
"""

import argparse
from pathlib import Path

from data import repository as repo
from data.db import connect
from reporting import deepdive
from schemas.models import Thesis

DRAFTS_DIR = Path(deepdive.OUTPUT_DIR).parent / "drafts"
BANNER = "> DRAFT SAMPLE RENDER — not a cycle artifact\n\n"


def main(thesis_id: int, week_key: str | None = None,
         out: str | None = None) -> Path:
    with connect() as conn:
        thesis = repo.fetch_one(conn, Thesis, "id = %s", (thesis_id,))
        if thesis is None:
            raise SystemExit(f"no thesis with id {thesis_id}")
        if week_key is None:
            iso = thesis.created_at.isocalendar()
            week_key = f"{iso.year}-W{iso.week:02d}"
        packet_path = deepdive.latest_packet_path(thesis.strategy_tag)
        content = deepdive.render(conn, thesis, week_key, packet_path)
    if out:
        path = Path(out)
    else:
        DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
        path = DRAFTS_DIR / (
            f"DRAFT-deepdive-{week_key}-{thesis.strategy_tag}-thesis{thesis.id}.md"
        )
    path.write_text(BANNER + content)
    print(f"wrote {path}")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("thesis_id", type=int)
    parser.add_argument("--week", default=None, help="week key, e.g. 2026-W29")
    parser.add_argument("--out", default=None, help="explicit output file path")
    args = parser.parse_args()
    main(args.thesis_id, week_key=args.week, out=args.out)
