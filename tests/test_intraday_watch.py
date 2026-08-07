"""Intraday Qwen watch (2026-08-07 P3, migration 013): batches stage scored
idea sketches for the NEXT nightly session — weekend batches map to Monday
via exchange_calendars, cross-batch dedup is a DB constraint, the nightly
attach pass re-issues sketches as [Sn] IDs that pass the citation precheck,
and resolution/expiry keep the table from growing stale. LLM, RSS, and
context building are mocked; DB is real.
"""

import json
from datetime import date, datetime, timezone

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate
from research import candidate_scout, idea_funnel, intraday_watch, proposal_checks
from tests.test_idea_funnel import gate_result, idea, make_ctx, slate

SESSION = date(2020, 1, 6)  # Monday sentinel: pre-genesis, inside XNYS calendar bounds


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def seed_experiment(conn) -> int:
    row = conn.execute(
        "INSERT INTO experiments (name, registry, created_at)"
        " VALUES ('EXP-TEST-INTRADAY', '{}', now())"
        " ON CONFLICT (name) DO UPDATE SET registry = EXCLUDED.registry"
        " RETURNING id").fetchone()
    return row[0]


def stub_batch(conn, monkeypatch, *, now, ideas, verdicts, ranking):
    monkeypatch.setattr(intraday_watch.rss, "ingest_all", lambda: {})
    monkeypatch.setattr(candidate_scout, "build_context",
                        lambda _conn: make_ctx())
    monkeypatch.setattr(
        intraday_watch.repo, "fetch_one",
        lambda *a, **k: type("E", (), {"id": seed_experiment(conn)})())
    outputs = iter([slate(*ideas), gate_result(verdicts, ranking)])
    monkeypatch.setattr(idea_funnel.llm, "structured_call",
                        lambda *a, **k: next(outputs))
    return intraday_watch.run_batch(conn, now)


def clear(conn):
    conn.execute("DELETE FROM idea_sketches WHERE session_date <= %s", (SESSION,))
    conn.execute("DELETE FROM scout_attempts WHERE session_date = %s", (SESSION,))


def test_weekend_batches_target_next_session():
    # 2020-01-04 was a Saturday; 2020-01-06 the next XNYS session (Monday)
    sat = datetime(2020, 1, 4, 12, 0, tzinfo=timezone.utc)
    assert intraday_watch.target_session(sat) == date(2020, 1, 6)
    # a session day targets itself (batches always fire before 23:00 ET)
    mon = datetime(2020, 1, 6, 16, 0, tzinfo=timezone.utc)
    assert intraday_watch.target_session(mon) == date(2020, 1, 6)


def test_batch_stages_survivors_and_dedups_across_batches(conn, monkeypatch):
    sat = datetime(2020, 1, 4, 12, 0, tzinfo=timezone.utc)
    with conn.transaction():
        clear(conn)
        summary = stub_batch(
            conn, monkeypatch, now=sat,
            ideas=[idea("Barge squeeze"), idea("Mine flood", domain="mining"),
                   idea("Priced out", ids=("H1",))],
            verdicts=[(1, "keep", "fresh"), (2, "keep", "fresh"),
                      (3, "kill", "price")], ranking=[2, 1])
        assert summary["session"] == SESSION
        assert summary["kept"] == 2 and summary["inserted"] == 2
        rows = conn.execute(
            "SELECT working_title, gate_rank, status, evidence FROM idea_sketches"
            " WHERE session_date = %s ORDER BY gate_rank", (SESSION,)).fetchall()
        assert [(r[0], r[1], r[2]) for r in rows] == [
            ("Mine flood", 1, "PENDING"), ("Barge squeeze", 2, "PENDING")]
        # evidence stores RESOLVED lines (marker stripped) + article links
        ev = rows[1][3]
        assert ev["ids"] == ["H1", "E1"]
        assert ev["lines"][0].startswith("Barge rates jump")
        assert ev["links"] == ["https://example.com/a1"]

        # a later batch re-proposing the same idea dedups on the constraint
        summary2 = stub_batch(
            conn, monkeypatch,
            now=datetime(2020, 1, 5, 12, 0, tzinfo=timezone.utc),
            ideas=[idea("Barge squeeze"), idea("B", domain="pharma"),
                   idea("C", domain="ag")],
            verdicts=[(1, "keep", "dup-but-kept"), (2, "kill", "x"),
                      (3, "kill", "x")], ranking=[1])
        assert summary2["kept"] == 1 and summary2["inserted"] == 0
        raise psycopg.Rollback


def test_attach_reissues_sn_ids_that_pass_citation_precheck(conn):
    with conn.transaction():
        clear(conn)
        conn.execute(
            "INSERT INTO idea_sketches (session_date, batch_key, working_title,"
            " hypothesis, domain, evidence, gate_rank, dedup_key) VALUES"
            " (%s, '2020-01-04T11', 'Barge squeeze', 'Rates rise.', 'shipping',"
            "  %s, 1, 'shipping:barge-squeeze'),"
            " (%s, '2020-01-03T15', 'Stale idea', 'Old.', 'ag', '{}', 1,"
            "  'ag:stale-idea')",
            (SESSION, json.dumps({"ids": ["H1"],
                                  "lines": ["Barge rates jump — Summary one."],
                                  "links": ["https://example.com/a1"]}),
             date(2020, 1, 3)))
        ctx = make_ctx()
        sid_map = intraday_watch.attach_staged_sketches(conn, ctx, SESSION)
        assert list(sid_map) == ["S1"]
        assert "STAGED INTRADAY IDEAS" in ctx.text
        assert "[S1] (staged 2020-01-04T11) Barge squeeze [shipping]" in ctx.text
        assert "S1" in ctx.issued_ids and "S1" in ctx.evidence_lines
        # the re-issued ID passes the deterministic citation gate
        assert proposal_checks.check_citations(
            ["[S1][H1] staged point", "[E1] trend"], ctx.issued_ids) == []
        # deep-evidence can pull the staged article via headline_items
        assert ctx.headline_items["S1"]["link"] == "https://example.com/a1"
        # the pre-session PENDING sketch was expired by the sweep
        status = conn.execute(
            "SELECT status FROM idea_sketches WHERE working_title = 'Stale idea'"
        ).fetchone()[0]
        assert status == "EXPIRED"
        raise psycopg.Rollback


def test_attach_with_empty_staging_is_a_noop(conn):
    with conn.transaction():
        clear(conn)
        ctx = make_ctx()
        before = ctx.text
        assert intraday_watch.attach_staged_sketches(conn, ctx, SESSION) == {}
        assert ctx.text == before
        raise psycopg.Rollback


def test_resolve_marks_cited_winner_and_passed_over(conn):
    with conn.transaction():
        clear(conn)
        conn.execute(
            "INSERT INTO idea_sketches (session_date, batch_key, working_title,"
            " hypothesis, domain, evidence, dedup_key) VALUES"
            " (%s, 'b', 'A', 'h', 'd', '{}', 'd:a'),"
            " (%s, 'b', 'B', 'h', 'd', '{}', 'd:b')", (SESSION, SESSION))
        ids = dict(conn.execute(
            "SELECT working_title, id FROM idea_sketches"
            " WHERE session_date = %s", (SESSION,)).fetchall())
        intraday_watch.resolve_sketches(
            conn, {"S1": ids["A"], "S2": ids["B"]}, {"S1", "H4"})
        statuses = dict(conn.execute(
            "SELECT working_title, status FROM idea_sketches"
            " WHERE session_date = %s", (SESSION,)).fetchall())
        assert statuses == {"A": "WINNER", "B": "PASSED_OVER"}
        raise psycopg.Rollback
