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


# ---------------------------------------------------------------- severity + score

def test_severity_anchors():
    from analytics.bubble_monitor import severity
    assert severity(D(0), D(0), D("0.5"), D(1)) == 0
    assert severity(D("0.25"), D(0), D("0.5"), D(1)) == 25
    assert severity(D("0.5"), D(0), D("0.5"), D(1)) == 50
    assert severity(D(1), D(0), D("0.5"), D(1)) == 85
    assert severity(D(9), D(0), D("0.5"), D(1)) == 100
    assert severity(None, D(0), D("0.5"), D(1)) is None


SCORE_CFG = {"weights": {c: "1" for c in "ABCDEFGHI"}, "max_weight": "0.5",
             "human_severity": {"GREEN": "15", "YELLOW": "50", "RED": "85"},
             "hard_red_floor": "50", "hard_red_step": "5",
             "bands": [["LOW", "30"], ["ELEVATED", "50"], ["HIGH", "70"], ["SEVERE", "100"]]}


def _sev(sid, link, sev):
    from analytics.bubble_monitor import SignalResult
    return SignalResult(sid, link, sid, RED, None, "", None, D(sev))


def test_composite_renormalizes_and_reports_coverage():
    from analytics.bubble_monitor import composite
    links = link_statuses([_sev("a", "A", 80), _sev("b", "A", 40), _sev("c", "B", 20)],
                          {}, today=TODAY, human_max_age_days=35)
    res = composite(links, 0, SCORE_CFG)
    # A = .5*80 + .5*60 = 70; B = 20; unscored links drop out
    assert res.score == 45 and res.band == "ELEVATED"
    assert res.coverage == D(2) / 9 and set(res.links) == {"A", "B"}


def test_human_score_counts_and_hard_red_floors():
    from analytics.bubble_monitor import composite
    human = {"G": HumanScore("G", RED, "n", TODAY)}
    links = link_statuses([], human, today=TODAY, human_max_age_days=35)
    assert composite(links, 0, SCORE_CFG).score == 85
    low = link_statuses([_sev("a", "A", 5)], {}, today=TODAY, human_max_age_days=35)
    assert composite(low, 0, SCORE_CFG).score == 5
    assert composite(low, 1, SCORE_CFG).score == 50
    assert composite(low, 3, SCORE_CFG).score == 60


def test_composite_empty():
    from analytics.bubble_monitor import composite
    links = link_statuses([], {}, today=TODAY, human_max_age_days=35)
    assert composite(links, 0, SCORE_CFG).score is None


# ---------------------------------------------------------------- fundamentals

def fact(start, end, val, filed="2026-01-01"):
    return {"start": start, "end": end, "val": val, "filed": filed}


def test_discrete_quarters_direct_and_ytd_difference():
    from analytics.bubble_monitor import discrete_quarters
    q = discrete_quarters([
        fact("2026-01-01", "2026-03-31", 10, "2026-04-20"),
        fact("2026-01-01", "2026-06-30", 25, "2026-07-20"),
        fact("2026-01-01", "2026-09-30", 45, "2026-10-20"),
        fact("2026-01-01", "2026-12-31", 70, "2027-02-01"),
    ])
    assert [q[date(2026, m, d)][0] for m, d in [(3, 31), (6, 30), (9, 30), (12, 31)]] == [10, 15, 20, 25]
    assert q[date(2026, 9, 30)][1] == date(2026, 10, 20)     # dated by filing


def test_discrete_quarters_ignores_unpairable_ttm():
    from analytics.bubble_monitor import discrete_quarters
    assert discrete_quarters([fact("2025-10-01", "2026-09-30", 100)]) == {}


def test_restated_value_wins_but_keeps_first_filed_date():
    from analytics.bubble_monitor import discrete_quarters
    q = discrete_quarters([fact("2026-01-01", "2026-03-31", 10, "2026-04-20"),
                           fact("2026-01-01", "2026-03-31", 12, "2027-04-20")])
    assert q[date(2026, 3, 31)] == (12, date(2026, 4, 20))


def test_oracle_fiscal_quarters_align_to_calendar_bucket():
    from analytics.bubble_monitor import _bucket, _prev_bucket
    assert _bucket(date(2026, 8, 31)) == date(2026, 9, 1)
    assert _bucket(date(2026, 9, 30)) == date(2026, 9, 1)
    assert _bucket(date(2026, 1, 25)) == date(2026, 3, 1)
    assert _prev_bucket(date(2026, 3, 1), 1) == date(2025, 12, 1)
    assert _prev_bucket(date(2026, 3, 1), 4) == date(2025, 3, 1)


def _quarters(year_from, n, val):
    out, y, m = [], year_from, 1
    for i in range(n):
        end_m = m + 2
        end = date(y, end_m, 30 if end_m in (6, 9) else 31)
        out.append(fact(f"{y}-{m:02d}-01", end.isoformat(), val(i), f"{end.year + (end_m == 12)}-{(end_m % 12) + 1:02d}-20"))
        m += 3
        if m > 12:
            y, m = y + 1, 1
    return out


def test_derive_fundamentals_ratios():
    from analytics.bubble_monitor import derive_fundamentals
    facts = {"AA": {"capex": _quarters(2024, 8, lambda i: 10 + i), "ocf": _quarters(2024, 8, lambda i: 20),
                    "revenue": _quarters(2024, 8, lambda i: 100), "dep": _quarters(2024, 8, lambda i: 5)},
             "BB": {"capex": _quarters(2024, 8, lambda i: 30), "ocf": _quarters(2024, 8, lambda i: 20),
                    "revenue": _quarters(2024, 8, lambda i: 100), "dep": _quarters(2024, 8, lambda i: 5)}}
    out = derive_fundamentals(facts, ["AA", "BB"], [])
    # final TTM: AA capex 14+15+16+17=62, BB 120; ocf 80+80
    assert out["hs_capex_ocf"][-1][1] == D(182) / 160
    assert out["hs_fcf_neg"][-1][1] == 1                      # BB only
    assert out["hs_capex_dep"][-1][1] == D(182) / 40
    assert out["hs_capex_rev"][-1][1] == D(182) / 800
    assert out["hs_capex_yoy"][-1][1] == D(47) / 43 - 1       # (17+30)/(13+30)


# ---------------------------------------------------------------- replay

def test_replay_is_point_in_time_and_honours_human_dates():
    from analytics.bubble_monitor import replay
    d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
    specs = [{"id": "w", "link": "A", "kind": "widening", "series": "S", "lookback": 5,
              "yellow": "0.5", "red": "1.0"}]
    dated = {"S": [(d1, D(1)), (d2, D(3))]}
    human = [HumanScore("B", RED, "n", d2)]
    pts = replay([d1, d2], dated, specs, human, [("capex_cut", d2, None)], score_cfg=SCORE_CFG,
                 human_max_age_days=35, confirm_signal_ids=[])
    assert pts[0].signals["w"] is None and pts[0].stage == 0   # d2's value is unseen on d1
    assert "B" not in pts[0].links
    assert pts[1].signals["w"] == 2 and pts[1].links["B"] == 85
    assert pts[1].stage == 1                                    # hard red + 2 reds both land at 1


# ---------------------------------------------------------------- stability

def test_held_stage_ignores_blips_and_needs_confirmation():
    from analytics.bubble_monitor import held_stages
    h = lambda raw, **k: held_stages(raw, confirm_up=3, confirm_down=4, **k)  # noqa: E731
    assert h([0, 2, 0, 2, 0, 0]) == [0] * 6                  # one-day flips never act
    assert h([2, 2, 2, 2]) == [0, 0, 2, 2]
    assert h([1, 1, 1, 2, 2, 2]) == [0, 0, 1, 1, 1, 2]


def test_held_stage_steps_down_slowly():
    from analytics.bubble_monitor import held_stages
    raw = [2, 2, 2, 0, 0, 0, 0]
    assert held_stages(raw, confirm_up=3, confirm_down=4) == [0, 0, 2, 2, 2, 2, 0]


def test_hard_red_floor_is_immediate():
    from analytics.bubble_monitor import held_stages
    assert held_stages([1, 1], confirm_up=5, confirm_down=10, floors=[0, 1]) == [0, 1]


def test_max_status_caps_status_not_severity():
    spec = {"id": "x", "link": "G", "kind": "level", "series": "S", "max_status": YELLOW,
            "calm": "0", "yellow": "0.25", "red": "0.35"}
    res = evaluate_signal(spec, {"S": seq(0.40)})
    assert res.status == YELLOW and res.severity > 85


def test_below_kind_low_is_bad():
    spec = {"id": "r", "link": "G", "kind": "below", "series": "S",
            "calm": "-1", "yellow": "-0.4", "red": "-0.2"}
    assert evaluate_signal(spec, {"S": seq(0.89)}).status == GREEN
    assert evaluate_signal(spec, {"S": seq(0.30)}).status == YELLOW
    assert evaluate_signal(spec, {"S": seq(0.10)}).status == RED


def test_inactive_link_is_off_the_chain_even_with_signals_or_a_human_score():
    from analytics.bubble_monitor import HumanScore
    human = {"B": HumanScore("B", RED, "n", TODAY)}
    links = link_statuses([_sev("a", "A", 80), _sev("c", "B", 20)], human,
                          today=TODAY, human_max_age_days=35, active={"A": "1", "C": "1"})
    assert [l.link for l in links] == ["A", "C"]
