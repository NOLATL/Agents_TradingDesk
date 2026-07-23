"""Monitoring never silently goes quiet (consolidation spec invariant 3):

every ACTIVE thesis gets a thesis_daily_status row each session — NO_CHANGE
is a written verdict; reversal signals escalate distinctly; a missing row
renders loudly as MONITOR GAP.
"""

from datetime import date

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate
from ops import approve, milestone_monitor
from reporting.daily import _monitoring_section, _theme_queue_section
from schemas.enums import MilestoneStatus, ThesisDailyVerdict, ThesisStatus
from schemas.models import Milestone
from data import repository as repo
from tests.test_theme_gate import NOW, make_thesis, propose

SESSION = date(2026, 7, 21)


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _open_todays_slot(conn):
    """Same isolation as test_theme_gate: the live dev DB may hold today's
    real accepted theme, and _active_thesis needs the slot open. Outer
    rollback transaction; inner test transactions become savepoints."""
    with conn.transaction(force_rollback=True):
        conn.execute(
            "UPDATE themes SET accepted_on = NULL WHERE accepted_on = %s",
            (approve.today_et(),),
        )
        yield


def _active_thesis(conn, slug):
    theme = propose(conn, slug)
    approve.accept_theme(conn, slug, commit=False)
    return theme, make_thesis(conn, theme, status=ThesisStatus.ACTIVE)


def test_no_evidence_still_writes_a_no_change_row(conn, monkeypatch):
    monkeypatch.setattr(milestone_monitor, "_recent_claims", lambda *a: [])
    with conn.transaction():
        _, thesis = _active_thesis(conn, "ms-quiet")
        results = milestone_monitor.record_daily_status(conn, SESSION, commit=False)
        assert (thesis.id, ThesisDailyVerdict.NO_CHANGE) in results
        row = conn.execute(
            "SELECT verdict, summary, post_trade FROM thesis_daily_status"
            " WHERE thesis_id = %s AND session_date = %s", (thesis.id, SESSION),
        ).fetchone()
        assert row[0] == "NO_CHANGE" and "no new evidence" in row[1]
        assert row[2] is False  # pre-trade
        raise psycopg.Rollback


def test_falsified_milestone_escalates_to_reversal(conn, monkeypatch):
    monkeypatch.setattr(milestone_monitor, "_recent_claims", lambda *a: [])
    monkeypatch.setattr(milestone_monitor, "notify", lambda *a, **k: True)
    with conn.transaction():
        _, thesis = _active_thesis(conn, "ms-reversal")
        milestone = Milestone(
            thesis_id=thesis.id, description="capex stays elevated",
            falsification_criterion="two guidance cuts",
            status=MilestoneStatus.FALSIFIED, created_at=NOW,
        )
        repo.insert(conn, milestone)
        milestone_monitor.record_daily_status(
            conn, SESSION, [(milestone, "FALSIFIED")], commit=False
        )
        verdict = conn.execute(
            "SELECT verdict FROM thesis_daily_status WHERE thesis_id = %s"
            " AND session_date = %s", (thesis.id, SESSION),
        ).fetchone()[0]
        assert verdict == "REVERSAL_SUSPECTED"
        raise psycopg.Rollback


def test_rerun_is_idempotent_upsert(conn, monkeypatch):
    monkeypatch.setattr(milestone_monitor, "_recent_claims", lambda *a: [])
    with conn.transaction():
        _, thesis = _active_thesis(conn, "ms-idem")
        milestone_monitor.record_daily_status(conn, SESSION, commit=False)
        milestone_monitor.record_daily_status(conn, SESSION, commit=False)
        n = conn.execute(
            "SELECT count(*) FROM thesis_daily_status WHERE thesis_id = %s",
            (thesis.id,),
        ).fetchone()[0]
        assert n == 1
        raise psycopg.Rollback


def test_report_orders_reversal_first_and_flags_gaps(conn, monkeypatch):
    monkeypatch.setattr(milestone_monitor, "_recent_claims", lambda *a: [])
    with conn.transaction():
        exp_id = conn.execute("SELECT id FROM experiments WHERE name='EXP-001'").fetchone()[0]
        _, quiet = _active_thesis(conn, "ms-a-quiet")
        theme_b = propose(conn, "ms-b-loud")
        loud = make_thesis(conn, theme_b, status=ThesisStatus.ACTIVE)
        _, gap = (None, make_thesis(conn, propose(conn, "ms-c-gap"),
                                    status=ThesisStatus.ACTIVE))
        milestone_monitor._write_status(
            conn, quiet.id, SESSION, ThesisDailyVerdict.NO_CHANGE,
            "no change (chain intact)", False, NOW)
        milestone_monitor._write_status(
            conn, loud.id, SESSION, ThesisDailyVerdict.REVERSAL_SUSPECTED,
            "broken: permitting link reversed", True, NOW)
        # gap thesis gets NO row on purpose

        section = "\n".join(_monitoring_section(conn, exp_id, SESSION))
        reversal_pos = section.find("REVERSAL SIGNAL")
        gap_pos = section.find("MONITOR GAP")
        quiet_pos = section.find("ms-a-quiet")
        assert reversal_pos != -1 and "ms-b-loud" in section and "post-trade" in section
        assert gap_pos != -1 and "ms-c-gap" in section
        assert quiet_pos != -1 and "no change" in section
        assert reversal_pos < quiet_pos  # escalations sort above routine lines
        raise psycopg.Rollback


def test_theme_queue_section_shows_slot_state(conn):
    with conn.transaction():
        propose(conn, "ms-slot")
        section = "\n".join(_theme_queue_section(conn, approve.today_et()))
        assert "**OPEN**" in section and "ms-slot" in section
        approve.accept_theme(conn, "ms-slot", commit=False)
        section = "\n".join(_theme_queue_section(conn, approve.today_et()))
        assert "**USED** by `ms-slot`" in section
        raise psycopg.Rollback
