"""Two-stage scout idea funnel (2026-07-29): idea slate + generous gate
write IDEA/IDEA_GATE instrument rows (migration 008), deterministic kills
override a hallucinating judge, all-killed returns None, and the deep
evidence stage pulls feed-full bodies / fetched articles / summary
fallbacks with budgets enforced. LLM and network are mocked; DB is real.
"""

from datetime import date

import psycopg
import pytest

from data.db import dsn
from data.migrate import migrate
from research import idea_funnel
from research.candidate_scout import ScoutContext

SESSION = date(2001, 1, 4)  # sentinel far outside real desk history


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


def make_ctx(**overrides):
    base = dict(
        text=("EXISTING THEMES (do NOT re-propose these or close variants):\n"
              "- grid-constraint: Grid theme\n\n"
              "RECENT HEADLINES:\n- [H1] Barge rates jump — Summary one.\n"
              "- [H2] Mine flooded — Summary two.\n\n"
              "EDGAR FULL-TEXT MENTION TRENDS:\n"
              '- [E1] "rare earth": ROSE 5 -> 9'),
        issued_ids={"H1", "H2", "E1"},
        edgar_directions={"E1": "ROSE"},
        headline_items={
            "H1": {"title": "Barge rates jump", "summary": "Summary one.",
                   "link": "https://example.com/a1", "body": "b" * 1500},
            "H2": {"title": "Mine flooded", "summary": "Summary two.",
                   "link": "https://example.com/a2", "body": "teaser"},
        },
        edgar_terms={"E1": "rare earth"},
        edgar_filers={"E1": [
            {"display": "Acme Rare Corp (ACME) (CIK 1)", "form": "10-K",
             "file_date": "2026-07-01"},
            {"display": "Acme Rare Corp (ACME) (CIK 1)", "form": "10-Q",
             "file_date": "2026-07-20"},
            {"display": "Basalt Mining (BSLT) (CIK 2)", "form": "10-Q",
             "file_date": "2026-07-10"},
        ]},
        evidence_lines={
            "H1": "- [H1] Barge rates jump — Summary one.",
            "H2": "- [H2] Mine flooded — Summary two.",
            "E1": '- [E1] "rare earth": ROSE 5 -> 9',
        },
    )
    base.update(overrides)
    return ScoutContext(**base)


def idea(title="Barge squeeze", ids=("H1", "E1"), domain="shipping"):
    return idea_funnel.CandidateIdea(
        working_title=title, hypothesis="Rates rise. Operators earn more.",
        evidence_ids=list(ids), domain=domain, non_overlap="Not grid.")


def slate(*ideas):
    return idea_funnel.IdeaSlate(ideas=list(ideas))


def gate_result(verdicts, ranking):
    return idea_funnel.IdeaGateResult(
        verdicts=[idea_funnel.IdeaVerdict(idea=i, verdict=v, reason=r)
                  for i, v, r in verdicts],
        ranking=ranking)


def clear_attempts(conn):
    conn.execute("DELETE FROM scout_attempts WHERE session_date = %s", (SESSION,))


def test_winner_path_writes_idea_and_gate_rows(conn, monkeypatch):
    outputs = iter([
        slate(idea("A"), idea("B", domain="mining"), idea("C", domain="pharma")),
        gate_result([(1, "keep", "fresh"), (2, "keep", "fresh"),
                     (3, "kill", "duplicate")], ranking=[2, 1]),
    ])
    monkeypatch.setattr(idea_funnel.llm, "structured_call",
                        lambda *a, **k: next(outputs))
    with conn.transaction():
        clear_attempts(conn)
        result = idea_funnel.run_idea_funnel(
            conn, experiment_id=1, ctx=make_ctx(), model_tag="m", session=SESSION)
        assert result is not None and result.idea.working_title == "B"
        assert "WINNING IDEA" in result.idea_block
        assert "DEEP EVIDENCE" in result.evidence_block
        rows = conn.execute(
            "SELECT stage, passed FROM scout_attempts WHERE session_date = %s"
            " ORDER BY id", (SESSION,)).fetchall()
        assert rows == [("IDEA", True), ("IDEA_GATE", True)]
        raise psycopg.Rollback


def test_all_killed_returns_none(conn, monkeypatch):
    outputs = iter([
        slate(idea("A"), idea("B"), idea("C")),
        gate_result([(1, "kill", "dup"), (2, "kill", "dup"),
                     (3, "kill", "price")], ranking=[]),
    ])
    monkeypatch.setattr(idea_funnel.llm, "structured_call",
                        lambda *a, **k: next(outputs))
    with conn.transaction():
        clear_attempts(conn)
        assert idea_funnel.run_idea_funnel(
            conn, experiment_id=1, ctx=make_ctx(), model_tag="m",
            session=SESSION) is None
        row = conn.execute(
            "SELECT passed FROM scout_attempts WHERE session_date = %s"
            " AND stage = 'IDEA_GATE'", (SESSION,)).fetchone()
        assert row == (False,)
        raise psycopg.Rollback


def test_invalid_ids_killed_despite_llm_keep(conn, monkeypatch):
    outputs = iter([
        slate(idea("A", ids=("H99",)), idea("B"), idea("C", ids=("H1",))),
        gate_result([(1, "keep", "looks fine"), (2, "kill", "dup"),
                     (3, "keep", "ok")], ranking=[1, 3]),
    ])
    monkeypatch.setattr(idea_funnel.llm, "structured_call",
                        lambda *a, **k: next(outputs))
    with conn.transaction():
        clear_attempts(conn)
        result = idea_funnel.run_idea_funnel(
            conn, experiment_id=1, ctx=make_ctx(), model_tag="m", session=SESSION)
        # idea 1 cited only nonexistent H99 → deterministically killed;
        # winner falls to idea 3 despite the judge ranking 1 first.
        assert result.idea.working_title == "C"
        raise psycopg.Rollback


def test_malformed_ranking_falls_back_to_slate_order(conn, monkeypatch):
    outputs = iter([
        slate(idea("A"), idea("B"), idea("C")),
        gate_result([(1, "keep", "ok"), (2, "keep", "ok"),
                     (3, "keep", "ok")], ranking=[7, 9]),
    ])
    monkeypatch.setattr(idea_funnel.llm, "structured_call",
                        lambda *a, **k: next(outputs))
    with conn.transaction():
        clear_attempts(conn)
        result = idea_funnel.run_idea_funnel(
            conn, experiment_id=1, ctx=make_ctx(), model_tag="m", session=SESSION)
        assert result.idea.working_title == "A"
        raise psycopg.Rollback


def test_article_text_three_statuses(conn, monkeypatch):
    ctx = make_ctx()
    # feed-full: body >= FEED_BODY_MIN, no fetch
    text, status = idea_funnel.article_text(conn, ctx.headline_items["H1"])
    assert status == "feed-full" and len(text) == 1500

    # fetched: short body, link fetch mocked
    class Doc:
        raw_bytes = ("<html><script>x()</script><p>"
                     + "Real article text. " * 100 + "</p></html>").encode()

    monkeypatch.setattr(idea_funnel, "fetch_and_cache",
                        lambda *a, **k: (Doc(), True))
    text, status = idea_funnel.article_text(conn, ctx.headline_items["H2"])
    assert status == "fetched" and "Real article text." in text and "x()" not in text

    # summary-only: fetch raises
    def boom(*a, **k):
        raise RuntimeError("403")

    monkeypatch.setattr(idea_funnel, "fetch_and_cache", boom)
    text, status = idea_funnel.article_text(conn, ctx.headline_items["H2"])
    assert status == "summary-only" and text == "Summary two."


def test_deep_evidence_renders_filers_and_fallbacks(conn, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("403")

    monkeypatch.setattr(idea_funnel, "fetch_and_cache", boom)
    block = idea_funnel.deep_evidence(
        conn, idea(ids=("H1", "H2", "E1")), make_ctx())
    assert "[H1] Barge rates jump — FULL TEXT EXCERPT (feed-full):" in block
    assert "[H2] Mine flooded — (full text unavailable) Summary two." in block
    assert "COMPANIES DRIVING THIS TREND:" in block
    assert "Acme Rare Corp (ACME) (CIK 1) — 2 filings, latest 10-Q 2026-07-20" in block


def test_deep_evidence_total_cap(conn):
    items = {f"H{i}": {"title": f"T{i}", "summary": "s",
                       "link": None, "body": "w" * 30000}
             for i in range(1, 8)}
    lines = {f"H{i}": f"- [H{i}] T{i}" for i in range(1, 8)}
    ctx = make_ctx(issued_ids=set(items), headline_items=items,
                   evidence_lines=lines, edgar_filers={}, edgar_terms={})
    block = idea_funnel.deep_evidence(
        conn, idea(ids=tuple(items)), ctx)
    # MAX_ARTICLES=5 and TOTAL_ARTICLE_CAP=18000 both bound the block
    assert block.count("FULL TEXT EXCERPT") <= idea_funnel.MAX_ARTICLES
    assert len(block) < idea_funnel.TOTAL_ARTICLE_CAP + 3000
