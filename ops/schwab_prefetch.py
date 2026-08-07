"""Friday 22:45 ET Schwab pre-fetch (Phase C, DECISIONS.md 2026-08-07):
theme-symbol quotes + fundamentals (plus their GICS-sub-industry peers, so
peer-percentile valuation works) fetched ~1h before the 23:45 weekly cycle,
so tonight's deep dives and theme briefs read SAME-DAY snapshots instead of
last Saturday's.

Best-effort by design: a dark or failed pre-fetch means the render falls
back to the newest older batch — a staleness note, never a failure. Exit 0
always (systemd unit stays green; degradation is a report fact).

    uv run python -m ops.schwab_prefetch
"""

import sys
from datetime import datetime, timezone

from config.loader import CONFIG
from data.adapters import schwab, wikipedia
from data.adapters.schwab_auth import SchwabDark
from data.db import connect
from ops.alerts import notify


def theme_universe_with_peers(conn) -> tuple[list[str], list[str]]:
    """(theme symbols, GICS-sub-industry peers) — peers bound the peer-PE
    distribution for the deep dive's valuation percentile."""
    theme_symbols = sorted(
        r[0] for r in conn.execute("SELECT DISTINCT symbol FROM theme_symbols"))
    sectors = wikipedia.sector_map(conn)
    subs = {sectors[s][1] for s in theme_symbols
            if s in sectors and sectors[s][1]}
    peers = sorted(t for t, (_, sub) in sectors.items()
                   if sub in subs and t not in theme_symbols)
    return theme_symbols, peers


def main() -> None:
    if not CONFIG.get("schwab", {}).get("enabled"):
        print("schwab disabled — nothing to pre-fetch")
        return
    tag = str(datetime.now(timezone.utc).date())
    try:
        with connect() as conn:
            theme_symbols, peers = theme_universe_with_peers(conn)
            if not theme_symbols:
                print("no theme symbols — nothing to pre-fetch")
                return
            schwab.ingest_quotes(conn, theme_symbols, session_tag=tag, kind="theme")
            conn.commit()
            docs = schwab.ingest_fundamentals(
                conn, theme_symbols + peers, session_tag=tag)
            print(f"pre-fetched quotes for {len(theme_symbols)} theme symbols,"
                  f" fundamentals for {len(theme_symbols) + len(peers)} symbols"
                  f" ({len(docs)} documents) — batch {tag}")
    except SchwabDark as e:
        print(f"schwab dark — deep dives will use the newest older batch: {e}",
              file=sys.stderr)
    except Exception as e:  # noqa: BLE001 — pre-fetch is best-effort
        print(f"pre-fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
        notify("Schwab pre-fetch failed",
               f"{type(e).__name__}: {str(e)[:200]} — tonight's deep dives"
               " fall back to the newest older snapshot",
               priority="default", tags="warning")


if __name__ == "__main__":
    main()
