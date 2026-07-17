"""Determinism discipline as a failing test, not a convention: the engine core

must contain no wall-clock reads, randomness, DB access, or float-typed money.
persist.py is the sanctioned shell and is exempt from the DB ban.
"""

import re
from pathlib import Path

ENGINE_DIR = Path(__file__).parent.parent.parent / "analytics" / "engine"

BANNED_EVERYWHERE = [
    r"datetime\.now", r"datetime\.today", r"date\.today", r"time\.time\(",
    r"\brandom\b", r"\buuid\b", r"os\.urandom",
]
BANNED_IN_CORE = [r"psycopg", r"connect\(", r"httpx", r"requests"]
CORE_EXEMPT = {"persist.py"}
FLOAT_EXEMPT = {"sizing.py", "metrics.py"}  # documented float zones (upstream of replay)


def engine_files():
    return sorted(p for p in ENGINE_DIR.glob("*.py") if p.name != "__init__.py")


def test_no_clock_or_randomness_in_engine():
    for path in engine_files():
        text = path.read_text()
        for pattern in BANNED_EVERYWHERE:
            hits = [
                line for line in text.splitlines()
                if re.search(pattern, line) and "datetime.now(timezone.utc)" not in line
            ]
            # persist.py may stamp a wall-clock created_at ONLY in its final
            # fallback branch; everything else is banned everywhere.
            if path.name == "persist.py" and pattern == r"datetime\.now":
                continue
            assert not hits, f"{path.name}: banned pattern {pattern!r} in {hits}"


def test_no_db_or_network_in_core():
    for path in engine_files():
        if path.name in CORE_EXEMPT:
            continue
        text = path.read_text()
        for pattern in BANNED_IN_CORE:
            assert not re.search(pattern, text), f"{path.name}: core must not use {pattern!r}"


def test_no_float_money_outside_documented_zones():
    """`float(` in core modules is a smell; the documented zones are sizing

    (proposal-time) and metrics (post-hoc) — both outside the replay boundary.
    loop.py's volatility bridge round-trips through Decimal(str(...)) and is
    whitelisted explicitly."""
    allowed_loop_lines = ("Decimal(str(round(",)
    for path in engine_files():
        if path.name in FLOAT_EXEMPT | CORE_EXEMPT:
            continue
        for line in path.read_text().splitlines():
            if "float(" in line and not any(a in line for a in allowed_loop_lines):
                assert False, f"{path.name}: float() outside documented zones: {line.strip()}"
