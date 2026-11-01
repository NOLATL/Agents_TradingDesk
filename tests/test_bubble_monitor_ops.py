"""AI-bubble exit monitor: pure rendering, stage-change alerts, CLI
validation, and (DB-gated) human tables + run persistence/idempotency.
Network- and LLM-free; DB tests use the dev database and skip without it."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest

from analytics.bubble_monitor import (
    GREEN,
    RED,
    YELLOW,
    HumanScore,
    SignalResult,
    link_statuses,
    stage,
)
from reporting.bubble_monitor import (
    QUARANTINE_BANNER,
    BubbleReport,
    HardRed,
    render_markdown,
    render_terminal,
    stage_change_message,
)

TODAY = date(2026, 11, 2)


def make_report(red_links=("A", "C"), previous=0, hard=()) -> BubbleReport:
    sigs = [SignalResult(f"s{c}", c, f"signal {c}", RED, Decimal("1"), f"reading {c}", TODAY)
            for c in red_links]
    sigs.append(SignalResult("spx_trend", "I", "S&P trend", GREEN, None, "above 200-day MA", TODAY))
    human = {"G": HumanScore("G", YELLOW, "Azure decel", TODAY),
             "H": HumanScore("H", RED, "old", date(2026, 8, 1))}
    links = link_statuses(sigs, human, today=TODAY, human_max_age_days=35)
    res = stage(links, list(hard), sigs, confirm_signal_ids=["spx_trend"])
    return BubbleReport(TODAY, links, res,
                        [HardRed(k, "desc", "note", TODAY) for k in hard],
                        previous, ["Schwab dark (test)"])


def test_markdown_has_banner_frontmatter_stage_and_gaps():
    md = render_markdown(make_report())
    assert md.startswith("---\ndate: 2026-11-02\ntype: bubble-monitor\nstage: 2")
    assert QUARANTINE_BANNER in md
    assert "STAGE 2 ▲ from 0 — DEFEND" in md
    assert "credit and capex links both red" in md
    assert "UNSCORED links" in md and "STALE human scores: H" in md
    assert "Schwab dark (test)" in md
    assert "| G Monetization | 🟡 YELLOW |" in md


def test_terminal_plain_and_color():
    r = make_report()
    plain = render_terminal(r, color=False)
    assert "\033[" not in plain and "STAGE 2" in plain and "UNSCORED:" in plain
    assert "\033[31m" in render_terminal(r, color=True)


def test_hard_red_renders_and_drives_stage_one():
    r = make_report(red_links=("F",), hard=("oracle_junk",))
    assert r.result.stage == 1
    assert "HARD RED oracle_junk" in render_terminal(r, color=False)
    assert "**oracle_junk**" in render_markdown(r)


def test_stage_change_message_only_on_move():
    assert stage_change_message(make_report(previous=2)) is None
    assert stage_change_message(make_report(previous=None)) is None
    title, body, prio = stage_change_message(make_report(previous=0))
    assert "0 → 2" in title and prio == "high"
    _, _, prio_down = stage_change_message(make_report(red_links=(), previous=2))
    assert prio_down == "default"


def test_score_cli_validates_before_touching_db(capsys):
    from ops import bubble_monitor as bm
    with pytest.raises(SystemExit):
        bm.score("Z", "RED", "x")
    with pytest.raises(SystemExit):
        bm.score("A", "PURPLE", "x")
    with pytest.raises(SystemExit):
        bm.score("A", "RED", "   ")


def test_config_signals_all_evaluate_on_empty_data():
    """Every configured signal parses and abstains (NO_DATA) on no data —
    a config typo fails here, not at 01:15."""
    from analytics.bubble_monitor import NO_DATA, evaluate_signal
    from config.loader import CONFIG
    cfg = CONFIG["bubble_monitor"]
    ids = [s["id"] for s in cfg["signals"]]
    assert len(ids) == len(set(ids))
    for spec in cfg["signals"]:
        assert evaluate_signal(spec, {}).status == NO_DATA
        names = [spec.get(k) for k in ("series", "vs", "spread", "equity")] + spec.get("members", [])
        for n in filter(None, names):
            assert n in cfg["fred_series"] or n in cfg["schwab_symbols"], (spec["id"], n)
    assert set(cfg["confirm_signal_ids"]) <= set(ids)


# ------------------------------------------------------------------ DB-gated

@pytest.fixture(scope="module")
def conn():
    from data.db import dsn
    from data.migrate import migrate
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.rollback()
    connection.close()


def test_human_scores_newest_wins_and_cleared_withdraws(conn):
    from ops.bubble_monitor import load_human
    conn.execute("DELETE FROM bubble_human_scores WHERE note LIKE 'test-%'")
    for status, note in [("YELLOW", "test-1"), ("RED", "test-2")]:
        conn.execute("INSERT INTO bubble_human_scores (link, status, note, recorded_on)"
                     " VALUES ('B', %s, %s, %s)", (status, note, TODAY))
    scores, _ = load_human(conn)
    assert scores["B"].status == "RED"
    conn.execute("INSERT INTO bubble_human_scores (link, status, note, recorded_on)"
                 " VALUES ('B', 'CLEARED', 'test-3', %s)", (TODAY,))
    scores, _ = load_human(conn)
    assert "B" not in scores
    conn.rollback()


def test_hard_red_single_active_per_key(conn):
    conn.execute("INSERT INTO bubble_hard_reds (key, note, raised_on) VALUES ('capex_cut', 'x', %s)",
                 (TODAY,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO bubble_hard_reds (key, note, raised_on)"
                     " VALUES ('capex_cut', 'y', %s)", (TODAY,))
    conn.rollback()


def test_persist_is_idempotent_upsert(conn):
    from ops.bubble_monitor import persist, previous_stage
    r = make_report()
    persist(conn, r)
    persist(conn, r)
    rows = conn.execute("SELECT stage, red_links FROM bubble_runs WHERE session = %s",
                        (TODAY,)).fetchall()
    assert rows == [(2, ["A", "C"])]
    assert previous_stage(conn, date(2026, 11, 3)) == 2
    conn.execute("DELETE FROM bubble_runs WHERE session = %s", (TODAY,))
    conn.commit()
