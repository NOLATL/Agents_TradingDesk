"""Momentum scan: isolation guards, scan cycle claims, degraded-run
semantics, pure render, and narrator containment. Network- and LLM-free —
HTTP and structured_call are faked; DB tests use the dev database."""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import psycopg
import pytest

from analytics.scan import PocketScore
from data import repository as repo
from data.db import dsn
from data.migrate import migrate
from data.price_bars import _latest_docs_by_symbol
from reporting.momentum_scan import (
    QUARANTINE_BANNER,
    PocketRow,
    ScanReportData,
    ThemeRow,
    narrator_inputs,
    render_scan,
)
from schemas.models import Document

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
D = Decimal


@pytest.fixture(scope="session")
def conn():
    try:
        connection = psycopg.connect(dsn(), connect_timeout=3)
    except psycopg.OperationalError:
        pytest.skip("dev Postgres not running — start it with: docker compose up -d --wait")
    migrate(connection)
    yield connection
    connection.close()


# --------------------------------------------------------------------------- #
# Isolation: tiingo_scan documents are invisible to every engine surface
# --------------------------------------------------------------------------- #

def scan_doc(symbol: str, rows: list[dict]) -> Document:
    return Document(
        content_hash=f"scan-test-{symbol}",
        raw_bytes=json.dumps(rows).encode(),
        retrieval_url=f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}/prices",
        source_name="tiingo_scan",
        ingestion_time=NOW,
        first_eligible_decision_time=NOW,
    )


def test_scan_documents_invisible_to_latest_docs_by_symbol(conn):
    with conn.transaction():
        repo.insert_document(conn, scan_doc("ZZSCN", [
            {"date": "2026-07-27T00:00:00.000Z", "open": 1, "high": 1, "low": 1,
             "close": 1, "adjClose": 1}]))
        assert "ZZSCN" not in _latest_docs_by_symbol(conn)
        raise psycopg.Rollback


def test_scan_cycle_claim_and_reclaim(conn):
    """claim/finish commit (ops-style), so this cleans up after itself."""
    from ops.momentum_scan import claim_scan_cycle, finish_scan_cycle

    try:
        cycle = claim_scan_cycle(conn, "2099-01-04", "test")
        assert cycle is not None and cycle.cycle_type == "scan"
        finish_scan_cycle(conn, cycle, "FAILED", "ingest", "boom")
        reclaimed = claim_scan_cycle(conn, "2099-01-04", "test")
        assert reclaimed is not None and reclaimed.id == cycle.id
        finish_scan_cycle(conn, reclaimed, "COMPLETE", "render")
        assert claim_scan_cycle(conn, "2099-01-04", "test") is None  # no-op
    finally:
        conn.rollback()
        conn.execute(
            "DELETE FROM cycle_log WHERE cycle_type='scan' AND period_key='2099-01-04'")
        conn.commit()


# --------------------------------------------------------------------------- #
# Ingest degradation: 429 stops the loop, other errors skip-and-note
# --------------------------------------------------------------------------- #

def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api.tiingo.com/x")
    return httpx.HTTPStatusError(
        "err", request=req, response=httpx.Response(status, request=req))


def test_ingest_stops_on_429_and_skips_rest(conn, monkeypatch):
    import ops.momentum_scan as ms

    calls = []

    def fake_ingest(conn_, symbol):
        calls.append(symbol)
        if symbol == "BBB":
            raise _http_error(429)
        return None, True

    alerts = []
    monkeypatch.setattr(ms, "ingest_scan_symbol", fake_ingest)
    monkeypatch.setattr(ms, "notify", lambda *a, **k: alerts.append((a, k)))
    fetched, cache_hits, gaps = ms._ingest_etfs(conn, ["AAA", "BBB", "CCC", "DDD"])
    assert calls == ["AAA", "BBB"]           # loop stopped, no retry
    assert fetched == 1
    assert gaps == [("BBB", "SKIPPED (rate limit)"), ("CCC", "SKIPPED (rate limit)"),
                    ("DDD", "SKIPPED (rate limit)")]
    assert alerts and alerts[0][1]["priority"] == "high"


def test_ingest_single_failure_is_contained(conn, monkeypatch):
    import ops.momentum_scan as ms

    def fake_ingest(conn_, symbol):
        if symbol == "BAD":
            raise _http_error(404)
        return None, True

    monkeypatch.setattr(ms, "ingest_scan_symbol", fake_ingest)
    monkeypatch.setattr(ms, "notify", lambda *a, **k: None)
    fetched, _, gaps = ms._ingest_etfs(conn, ["AAA", "BAD", "CCC"])
    assert fetched == 2
    assert gaps == [("BAD", "fetch failed (HTTP 404)")]


def test_ingest_respects_fetch_cap(conn, monkeypatch):
    import ops.momentum_scan as ms

    monkeypatch.setitem(ms.SCAN_CFG, "max_fetches_per_run", 2)
    monkeypatch.setattr(ms, "ingest_scan_symbol", lambda c, s: (None, True))
    fetched, _, gaps = ms._ingest_etfs(conn, ["A", "B", "C", "D"])
    assert fetched == 2
    assert gaps == [("C", "SKIPPED (fetch cap)"), ("D", "SKIPPED (fetch cap)")]


# --------------------------------------------------------------------------- #
# Pure render
# --------------------------------------------------------------------------- #

def _pocket(ticker, name, kind, composite, rets, delta):
    n = len(rets)
    return PocketRow(
        ticker=ticker, name=name, kind=kind, delta=delta,
        score=PocketScore(symbol=ticker,
                          returns=tuple(None if r is None else D(str(r)) for r in rets),
                          zscores=tuple([D("0.1")] * n),
                          composite=D(str(composite))),
    )


def _data(**overrides):
    base = dict(
        session=date(2026, 7, 28),
        horizons=(5, 21, 63),
        pockets=(
            _pocket("URA", "Uranium", "theme-etf", "1.42", ["0.032", "0.081", "0.155"], None),
            _pocket("XLI", "Industrials", "sector", "0.65", ["0.011", "0.024", "0.070"], 6),
            _pocket("XLE", "Energy", "sector", "-0.90", ["-0.021", "-0.035", "-0.010"], -2),
        ),
        theme_sections=(
            ("grid-constraint", (
                ThemeRow(symbol="ETN", gics_sector="Industrials",
                         gics_sub_industry="Electrical Components & Equipment",
                         score=PocketScore(symbol="ETN",
                                           returns=(D("0.02"), D("0.05"), D("0.11")),
                                           zscores=(D("0.5"), D("0.4"), D("0.3")),
                                           composite=D("0.40"))),
                ThemeRow(symbol="ZQQQ", gics_sector=None, gics_sub_industry=None,
                         score=PocketScore(symbol="ZQQQ",
                                           returns=(D("-0.01"), None, None),
                                           zscores=(D("-0.5"), None, None),
                                           composite=None)),
            )),
        ),
        hot_pocket_members=(("Uranium (URA)", ("CEG — Constellation Energy",)),),
        gaps=(("BOAT", "SKIPPED (rate limit)"),),
        unscored=("TAN",),
        prior_session=date(2026, 7, 27),
        fetched=30, cache_hits=8,
        snapshot_refreshed=NOW,
    )
    base.update(overrides)
    return ScanReportData(**base)


def test_render_carries_banner_and_sections():
    text = render_scan(_data())
    assert QUARANTINE_BANNER in text
    assert "## Pocket leaderboard" in text
    assert "| 1 | NEW | Uranium | theme-etf | URA | +1.42 | +3.2% | +8.1% | +15.5% |" in text
    assert "## Movers vs prior scan" in text
    assert "**Industrials** (XLI) up 6 to #2" in text
    assert "## Sector snapshot" in text
    assert "## Desk themes → GICS pockets" in text
    assert "Industrials / Electrical Components & Equipment" in text
    assert "not in S&P 500" in text
    assert "Breadth: 1/2 positive 5d" in text
    assert "## Inside the hot pockets" in text
    assert "- BOAT: SKIPPED (rate limit)" in text
    assert "insufficient history" in text and "TAN" in text
    assert "fetched 30, cache-hit 8" in text


def test_render_without_narration_notes_absence():
    text = render_scan(_data(narration=None, narration_note="Ollama down"))
    assert "*Narration unavailable: Ollama down*" in text
    assert "## Summary" not in text


def test_render_with_narration_labels_judgment():
    text = render_scan(_data(narration="Momentum concentrated in Uranium."))
    assert "## Summary" in text
    assert "judgment not fact" in text
    assert "Momentum concentrated in Uranium." in text


# --------------------------------------------------------------------------- #
# Narrator containment (no LLM: structured_call faked)
# --------------------------------------------------------------------------- #

def test_narrator_inputs_expose_all_quotable_numbers():
    context, values, symbols = narrator_inputs(_data())
    assert "Uranium (URA, theme-etf) composite +1.42" in context
    assert {"URA", "XLI", "XLE", "ETN", "ZQQQ"} <= symbols
    assert 1.42 in values or any(abs(v - 1.42) < 1e-9 for v in values)
    assert any(abs(v - 3.2) < 1e-9 for v in values)   # 0.032 quoted as 3.2(%)


def test_narrate_scan_retries_on_untraceable_number_then_drops(conn, monkeypatch):
    from agents.roles import scan_narrator
    from agents.state import SectionDraft

    drafts = iter(["Uranium leads with +9.99% this week.",   # fabricated figure
                   "Uranium leads, up 3.2% on the week."])   # traceable
    prompts = []

    def fake_call(conn_, **kwargs):
        prompts.append(kwargs["user_prompt"])
        return SectionDraft(text=next(drafts))

    monkeypatch.setattr(scan_narrator.llm, "structured_call", fake_call)
    context, values, symbols = narrator_inputs(_data())
    text, note = scan_narrator.narrate_scan(
        conn, experiment_id=1, context_text=context, values=values,
        allowed_symbols=symbols, known_symbols=symbols | {"NVDA"})
    assert text == "Uranium leads, up 3.2% on the week."
    assert note is None
    assert len(prompts) == 2 and "rejected by mechanical validation" in prompts[1]


def test_narrate_scan_rejects_foreign_ticker_and_gives_up(conn, monkeypatch):
    from agents.roles import scan_narrator
    from agents.state import SectionDraft

    monkeypatch.setattr(
        scan_narrator.llm, "structured_call",
        lambda conn_, **k: SectionDraft(text="Rotate into NVDA now."))
    context, values, symbols = narrator_inputs(_data())
    text, note = scan_narrator.narrate_scan(
        conn, experiment_id=1, context_text=context, values=values,
        allowed_symbols=symbols, known_symbols=symbols | {"NVDA"},
        max_attempts=2)
    assert text is None
    assert "dropped after 2 attempts" in note and "NVDA" in note


# --------------------------------------------------------------------------- #
# Phase A (three-vendor architecture): internals render + quarantine guards
# --------------------------------------------------------------------------- #

def _internals_row(pocket="Semiconductors"):
    from analytics.scan_internals import PocketInternals
    return PocketInternals(
        pocket=pocket, members_scored=8, members_total=9,
        median_member_composite=D("0.42"), breadth_pos=6, breadth_known=8,
        pct_above_avg=D("0.75"), new_highs=2, new_lows=1,
        dispersion=D("0.031"))


def test_render_internals_and_referee_sections():
    text = render_scan(_data(
        internals=(_internals_row(),), index_internals=_internals_row("S&P 500"),
        internals_note="alpaca pages fetched 12, cache-hit 0",
        referee_notes=("no Tiingo-vs-Alpaca divergence beyond tolerance",)))
    assert "## Pocket internals" in text
    assert "| S&P 500 | 8/9 | +0.42 | 6/8 | 75% | 2/1 | 3.1% |" in text
    assert "| Semiconductors | 8/9" in text
    assert "*alpaca pages fetched 12, cache-hit 0*" in text
    assert "## Cross-vendor referee" in text
    assert "no Tiingo-vs-Alpaca divergence" in text


def test_render_without_internals_hides_sections():
    text = render_scan(_data())
    assert "## Pocket internals" not in text
    assert "## Cross-vendor referee" not in text


def test_narrator_inputs_carry_internals_values():
    context, values, _ = narrator_inputs(_data(
        internals=(_internals_row(),), index_internals=None))
    assert "POCKET INTERNALS" in context
    assert "Semiconductors: 8/9 scored" in context
    assert any(abs(v - 75.0) < 1e-9 for v in values)   # pct_above_avg as %
    assert any(abs(v - 3.1) < 1e-9 for v in values)    # dispersion as %


def test_alpaca_documents_never_enter_price_bars(conn):
    """Extends the source allowlist regression: the rebuild is blind to
    every non-'tiingo' source_name (invariant-5 isolation)."""
    from data.price_bars import rebuild_price_bars, table_fingerprint

    with conn.transaction():
        rebuild_price_bars(conn)
        fp = table_fingerprint(conn)
        repo.insert_document(conn, Document(
            content_hash="alp-pb-guard", raw_bytes=b'{"bars": {}}',
            retrieval_url="https://data.alpaca.markets/v2/stocks/bars",
            source_name="alpaca_scan", ingestion_time=NOW,
            first_eligible_decision_time=NOW))
        rebuild_price_bars(conn)
        assert table_fingerprint(conn) == fp
        raise psycopg.Rollback


def test_engine_roles_import_no_scan_vendors():
    """Static blinding guard (EXP-001 freeze): the engine roles and scout
    must not import the scan vendors or scan analytics."""
    from pathlib import Path

    root = Path(__file__).parent.parent
    banned = ("adapters.alpaca", "adapters.schwab", "analytics.scan",
              "adapters import alpaca", "adapters import schwab")
    for rel in ("agents/roles/evidence.py", "agents/roles/analyst.py",
                "agents/roles/reviewer.py", "research/candidate_scout.py"):
        src = (root / rel).read_text()
        for token in banned:
            assert token not in src, f"{rel} references {token}"
