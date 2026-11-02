"""AI-bubble exit monitor: pure signal math, link aggregation, staging rule.
Network-, DB- and LLM-free (analytics/bubble_monitor.py)."""

from datetime import date
from decimal import Decimal

import pytest

from analytics.bubble_monitor import (
    GREEN,
    NO_DATA,
    RED,
    YELLOW,
    HumanScore,
    below_moving_average,
    drawdown_from_high,
    evaluate_signal,
    grade,
    link_statuses,
    pct_change,
    stage,
    widening,
)

D = Decimal
TODAY = date(2026, 11, 2)


def seq(*xs) -> list[Decimal]:
    return [D(str(x)) for x in xs]


# ---------------------------------------------------------------- primitives

def test_widening_vs_trailing_min():
    assert widening(seq(3.0, 2.8, 2.9, 3.9), 4) == D("1.1")
    # lookback excludes the older, tighter observation
    assert widening(seq(1.0, 3.0, 3.2, 3.5), 3) == D("0.5")
    assert widening(seq(3.0), 5) is None


def test_pct_change_and_abstain():
    assert pct_change(seq(100, 110), 1) == D("0.1")
    assert pct_change(seq(100), 1) is None
    assert pct_change(seq(0, 5), 1) is None


def test_drawdown_from_high():
    assert drawdown_from_high(seq(100, 120, 90), 3) == D("0.25")
    assert drawdown_from_high(seq(100, 120, 90), 1) == 0
    assert drawdown_from_high([], 5) is None


def test_below_ma_requires_full_window():
    assert below_moving_average(seq(1, 2, 3), 4) is None
    assert below_moving_average(seq(10, 10, 10, 7), 4) is True
    assert below_moving_average(seq(10, 10, 10, 12), 4) is False


def test_grade_bigger_is_worse():
    assert grade(D("1.2"), D("0.5"), D("1.0")) == RED
    assert grade(D("1.0"), D("0.5"), D("1.0")) == RED            # inclusive
    assert grade(D("0.6"), D("0.5"), D("1.0")) == YELLOW
    assert grade(D("0.1"), D("0.5"), D("1.0")) == GREEN
    assert grade(None, D("0.5"), D("1.0")) == NO_DATA


# ---------------------------------------------------------------- signals

def test_widening_signal_hy_oas():
    spec = {"id": "hy", "link": "A", "kind": "widening", "series": "HY",
            "lookback": 5, "yellow": "0.5", "red": "1.0"}
    res = evaluate_signal(spec, {"HY": seq(3.0, 2.9, 2.8, 3.2, 3.9)},
                          {"HY": TODAY})
    assert res.status == RED and res.measure == D("1.1") and res.as_of == TODAY


def test_fall_pct_signal_orients_declines_positive():
    spec = {"id": "jpy", "link": "A", "kind": "fall_pct", "series": "USDJPY",
            "lookback": 2, "yellow": "0.03", "red": "0.05"}
    res = evaluate_signal(spec, {"USDJPY": seq(150, 149, 141)})
    assert res.status == RED and res.measure == D("0.06")
    # a rally is GREEN, not an error
    assert evaluate_signal(spec, {"USDJPY": seq(140, 145, 150)}).status == GREEN


def test_rel_return_underperformance():
    spec = {"id": "bdc", "link": "D", "kind": "rel_return", "series": "BIZD",
            "vs": "SPY", "lookback": 2, "yellow": "0.05", "red": "0.10"}
    # BIZD -8%, SPY +2% → 10% underperformance → RED (inclusive)
    res = evaluate_signal(spec, {"BIZD": seq(100, 95, 92), "SPY": seq(100, 101, 102)})
    assert res.measure == D("0.10") and res.status == RED
    # BIZD -4%, SPY +2% → 6% → YELLOW
    res = evaluate_signal(spec, {"BIZD": seq(100, 98, 96), "SPY": seq(100, 101, 102)})
    assert res.measure == D("0.06") and res.status == YELLOW


def test_missing_series_is_no_data_not_green():
    spec = {"id": "x", "link": "D", "kind": "drawdown", "series": "HYG",
            "window": 10, "yellow": "0.02", "red": "0.04"}
    assert evaluate_signal(spec, {}).status == NO_DATA


def test_basket_drawdown_mean_of_present_members():
    spec = {"id": "periph", "link": "D", "kind": "basket_drawdown",
            "members": ["ORCL", "CRWV", "MISSING"], "window": 5,
            "yellow": "0.30", "red": "0.50"}
    res = evaluate_signal(spec, {"ORCL": seq(100, 80), "CRWV": seq(100, 40)})
    assert res.measure == D("0.4") and res.status == YELLOW
    assert "2/3 names" in res.detail and "worst CRWV" in res.detail


def test_below_ma_red_needs_drawdown():
    spec = {"id": "spx", "link": "I", "kind": "below_ma", "series": "SPX",
            "window": 3, "red_drawdown": "0.20"}
    assert evaluate_signal(spec, {"SPX": seq(100, 100, 95)}).status == YELLOW
    assert evaluate_signal(spec, {"SPX": seq(100, 100, 75)}).status == RED
    assert evaluate_signal(spec, {"SPX": seq(100, 100, 105)}).status == GREEN
    assert evaluate_signal(spec, {"SPX": seq(100)}).status == NO_DATA


def test_divergence_credit_wide_equity_at_high():
    spec = {"id": "div", "link": "A", "kind": "divergence", "spread": "HY",
            "spread_lookback": 5, "spread_widening": "0.5",
            "equity": "SPX", "equity_window": 5, "near_high": "0.02"}
    diverging = {"HY": seq(3.0, 3.1, 3.6), "SPX": seq(100, 101, 102)}
    assert evaluate_signal(spec, diverging).status == RED
    both_falling = {"HY": seq(3.0, 3.1, 3.6), "SPX": seq(100, 101, 90)}
    assert evaluate_signal(spec, both_falling).status == GREEN


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        evaluate_signal({"id": "x", "link": "A", "kind": "vibes"}, {})


# ---------------------------------------------------------------- links

def _sig(sid, link, status):
    from analytics.bubble_monitor import SignalResult
    return SignalResult(sid, link, sid, status, None, "")


def test_link_status_worst_of_auto_and_fresh_human():
    sigs = [_sig("a1", "A", YELLOW), _sig("a2", "A", GREEN), _sig("d1", "D", NO_DATA)]
    human = {"A": HumanScore("A", RED, "fed hike", TODAY),
             "C": HumanScore("C", YELLOW, "guide flat", date(2026, 9, 1))}
    links = {l.link: l for l in link_statuses(sigs, human, today=TODAY,
                                              human_max_age_days=35)}
    assert links["A"].status == RED and links["A"].auto == YELLOW
    assert links["C"].human_stale and links["C"].status == NO_DATA   # stale ignored
    assert links["D"].status == NO_DATA                              # never silently GREEN
    assert links["B"].status == NO_DATA and links["B"].human is None


def test_human_green_does_not_mask_auto_red():
    sigs = [_sig("a1", "A", RED)]
    human = {"A": HumanScore("A", GREEN, "fine", TODAY)}
    (a,) = [l for l in link_statuses(sigs, human, today=TODAY, human_max_age_days=35)
            if l.link == "A"]
    assert a.status == RED


# ---------------------------------------------------------------- stage

def _links(red_codes):
    sigs = [_sig(f"s{c}", c, RED) for c in red_codes]
    return link_statuses(sigs, {}, today=TODAY, human_max_age_days=35), sigs


def test_stage_zero_one_red():
    links, sigs = _links(["A"])
    assert stage(links, [], sigs, confirm_signal_ids=["sA"]).stage == 0


def test_stage_one_two_reds_or_hard_red():
    links, sigs = _links(["A", "F"])
    assert stage(links, [], sigs, confirm_signal_ids=[]).stage == 1
    links, sigs = _links([])
    res = stage(links, ["oracle_junk"], sigs, confirm_signal_ids=[])
    assert res.stage == 1 and "oracle_junk" in res.reasons[0]


def test_hard_red_alone_never_exceeds_stage_one():
    links, sigs = _links(["F"])
    assert stage(links, ["a", "b", "c"], sigs, confirm_signal_ids=[]).stage == 1


def test_stage_two_three_reds_or_credit_plus_capex():
    links, sigs = _links(["A", "F", "I"])
    assert stage(links, [], sigs, confirm_signal_ids=["missing"]).stage == 2
    links, sigs = _links(["D", "C"])
    res = stage(links, [], sigs, confirm_signal_ids=[])
    assert res.stage == 2 and "credit and capex" in res.reasons[0]


def test_stage_three_needs_market_confirmation():
    links, sigs = _links(["A", "C", "I"])
    sigs_conf = sigs + [_sig("spx_ma", "I", YELLOW)]
    res = stage(links, [], sigs_conf, confirm_signal_ids=["spx_ma", "sA"])
    assert res.stage == 3 and res.market_confirmed
    sigs_noconf = sigs + [_sig("spx_ma", "I", GREEN)]
    assert stage(links, [], sigs_noconf, confirm_signal_ids=["spx_ma", "sA"]).stage == 2
    # empty confirm list can never confirm
    assert stage(links, [], sigs, confirm_signal_ids=[]).stage == 2
