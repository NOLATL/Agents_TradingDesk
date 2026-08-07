"""Market-context analytics (valuation snapshot, 52wk positioning, SI
crowding) + the deep-dive Market context section (deterministic render,
Schwab faked). Network-free."""

from decimal import Decimal
from types import SimpleNamespace

from analytics.market_context import (
    days_to_cover_flag,
    range_position,
    short_interest_crowding,
    valuation_snapshot,
)

D = Decimal


def test_range_position_edges():
    assert range_position(D(150), D(100), D(200)) == D("0.5")
    assert range_position(D(90), D(100), D(200)) == 0     # clamped below
    assert range_position(D(250), D(100), D(200)) == 1    # clamped above
    assert range_position(None, D(100), D(200)) is None
    assert range_position(D(150), D(200), D(200)) is None  # degenerate range


def _snap(**overrides):
    base = dict(
        vendor_pe=D("20"), pb=D("3"), peg=D("1.5"), div_yield=D("1.2"),
        market_cap=D("1000000"), beta=D("1.1"),
        short_int_pct_float=D("2.5"), short_int_days_to_cover=D("3"),
        last=D("100"), lo52=D("80"), hi52=D("120"), eps_ttm=D("5"),
        peer_pes=[D("10"), D("20"), D("30")],
    )
    base.update(overrides)
    return valuation_snapshot("TST", **base)


def test_valuation_snapshot_assembles_and_ranks():
    s = _snap()
    assert s.pe == D("20")
    assert s.range_position_52wk == D("0.5")
    assert s.pe_pctile_vs_peers == D(2) / 3   # 10 and 20 are <= 20
    assert s.pe_crosscheck_note is None       # 100/5 = 20 == vendor


def test_valuation_snapshot_flags_vendor_pe_mismatch():
    s = _snap(vendor_pe=D("30"))              # computed 20 vs vendor 30
    assert s.pe_crosscheck_note is not None
    assert "vendor PE 30" in s.pe_crosscheck_note


def test_valuation_snapshot_tolerates_missing_inputs():
    s = _snap(vendor_pe=None, last=None, eps_ttm=None, peer_pes=[])
    assert s.pe is None and s.pe_pctile_vs_peers is None
    assert s.pe_crosscheck_note is None


def test_short_interest_crowding_median_and_z():
    si = {"A": D("2"), "B": D("4"), "C": D("10"), "D": D("30"), "E": D("1")}
    z = short_interest_crowding(
        si, {"low": ["A", "B", "E"], "high": ["C", "D"], "empty": ["ZZ"]})
    assert set(z) == {"low", "high"}          # empty pocket dropped
    assert z["high"] > 0 > z["low"]


def test_days_to_cover_flag():
    assert days_to_cover_flag(D("9"), D("8"))
    assert not days_to_cover_flag(D("7.9"), D("8"))
    assert not days_to_cover_flag(None, D("8"))


# --------------------------------------------------------------------------- #
# Deep-dive Market context section (pure render, Schwab faked)
# --------------------------------------------------------------------------- #

def _fake_schwab(monkeypatch, *, funds, quotes, tape):
    from data.adapters import schwab

    monkeypatch.setattr(schwab, "latest_fundamentals", lambda c: (funds, "2026-08-07"))
    monkeypatch.setattr(
        schwab, "latest_quotes",
        lambda c, kind: (tape, "2026-08-07") if kind == "tape" else (quotes, "2026-08-07"))
    monkeypatch.setattr(schwab, "latest_index_history", lambda c, s: [])


def _bundle(symbols):
    return SimpleNamespace(theme=SimpleNamespace(
        research_config={"symbols": symbols}))


def test_market_context_lines_render_table(monkeypatch):
    from data.adapters.schwab import FundamentalsSnap, QuoteSnap
    from reporting.deepdive import _market_context_lines

    monkeypatch.setitem(
        __import__("config.loader", fromlist=["CONFIG"]).CONFIG,
        "schwab", {"enabled": True, "vix_history": {"symbol": "$VIX"},
                   "regime_bands": [], "crowding": {"days_to_cover_flag": "8"},
                   "fundamentals": {"pe_crosscheck_tolerance_pct": "5"}})
    funds = {"ETN": FundamentalsSnap(
        symbol="ETN", pe=D("30"), pb=D("5"), peg=D("2.1"), div_yield=D("1.1"),
        eps_ttm=D("10"), market_cap=D("120000"), beta=D("1.05"),
        short_int_pct_float=D("1.4"), short_int_days_to_cover=D("9"),
        high_52wk=D("380"), low_52wk=D("250"))}
    quotes = {"ETN": QuoteSnap(symbol="ETN", last=D("300"),
                               net_pct_change=D("0.5"), close=D("299"),
                               high_52wk=D("380"), low_52wk=D("250"),
                               total_volume=1000, description="Eaton")}
    _fake_schwab(monkeypatch, funds=funds, quotes=quotes, tape={})
    lines = _market_context_lines(object(), _bundle(["ETN", "ZZNONE"]))
    text = "\n".join(lines)
    assert "## Market context (reviewer-domain — quarantined)" in text
    assert "No LLM touches this section" in text
    assert "| ETN | 30.0" in text
    assert "38%" in text                       # (300-250)/(380-250) 52wk pos
    assert "**9.0** ⚠" in text                 # days-to-cover flag
    assert "ZZNONE" not in text                # absent data absent, not faked


def test_market_context_dark_renders_note(monkeypatch):
    from reporting.deepdive import _market_context_lines

    monkeypatch.setitem(
        __import__("config.loader", fromlist=["CONFIG"]).CONFIG,
        "schwab", {"enabled": True})
    from data.adapters import schwab

    def boom(*a, **k):
        raise RuntimeError("db offline")

    monkeypatch.setattr(schwab, "latest_quotes", boom)
    lines = _market_context_lines(object(), _bundle(["ETN"]))
    assert any("Market context unavailable" in ln for ln in lines)


def test_market_context_disabled_renders_nothing(monkeypatch):
    from reporting.deepdive import _market_context_lines

    monkeypatch.setitem(
        __import__("config.loader", fromlist=["CONFIG"]).CONFIG,
        "schwab", {"enabled": False})
    assert _market_context_lines(object(), _bundle(["ETN"])) == []
