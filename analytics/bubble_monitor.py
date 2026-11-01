"""AI-bubble exit monitor math — operator-only, QUARANTINED from the analyst
(invariant 5): price-derived signals here feed a report for the HUMAN's
personal de-risking decision and never reach evidence, baskets, or the
engine. DECISIONS.md 2026-11-01 "AI-bubble exit monitor".

Pure functions: Decimal in, Decimal/str out, no DB, no network. Every
threshold arrives from config (`bubble_monitor:` in experiment.yaml); the
human owns the de-risk decision — `stage()` produces a *recommendation
level* from pre-committed rules, it places nothing.

Vocabulary (docs/themes/monitor-ai-bubble-exit.md §3):
- signal: one automated indicator → GREEN / YELLOW / RED / NO_DATA
- link: one of the nine chain links A..I; status = worst of its automated
  signals and the human's (non-stale) score
- stage 0..3: count of RED links, hard reds, market confirmation
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from analytics.momentum import QUARANTINE_KEY

__all__ = [
    "QUARANTINE_KEY", "GREEN", "YELLOW", "RED", "NO_DATA", "LINKS",
    "SignalResult", "LinkStatus", "StageResult",
    "widening", "pct_change", "drawdown_from_high", "trailing_return",
    "below_moving_average", "grade", "evaluate_signal", "link_statuses",
    "stage",
]

GREEN, YELLOW, RED, NO_DATA = "GREEN", "YELLOW", "RED", "NO_DATA"
_SEVERITY = {NO_DATA: -1, GREEN: 0, YELLOW: 1, RED: 2}

LINKS: dict[str, str] = {
    "A": "Funding regime",
    "B": "Capital into AI",
    "C": "Hyperscaler capex",
    "D": "Financing plumbing",
    "E": "Physical build",
    "F": "Compute pricing / semis",
    "G": "Monetization",
    "H": "Accounting quality",
    "I": "Market structure",
}


def _worst(statuses: Sequence[str]) -> str:
    real = [s for s in statuses if s != NO_DATA]
    if not real:
        return NO_DATA
    return max(real, key=_SEVERITY.__getitem__)


# --------------------------------------------------------------------------- #
# Primitive measures (ascending series; None = abstain, never a guess)
# --------------------------------------------------------------------------- #

def widening(values: Sequence[Decimal], lookback: int) -> Decimal | None:
    """Latest minus the minimum of the trailing `lookback` observations
    (inclusive of latest). For spreads: how far off the recent tight."""
    if len(values) < 2:
        return None
    window = values[-lookback:]
    return values[-1] - min(window)


def pct_change(values: Sequence[Decimal], lookback: int) -> Decimal | None:
    """Fractional change from `lookback` observations ago to latest."""
    if len(values) <= lookback or values[-1 - lookback] == 0:
        return None
    return values[-1] / values[-1 - lookback] - 1


def trailing_return(values: Sequence[Decimal], lookback: int) -> Decimal | None:
    return pct_change(values, lookback)


def drawdown_from_high(values: Sequence[Decimal], window: int) -> Decimal | None:
    """Fraction below the trailing-`window` high (0 = at the high)."""
    if not values:
        return None
    hi = max(values[-window:])
    if hi <= 0:
        return None
    return 1 - values[-1] / hi


def below_moving_average(values: Sequence[Decimal], window: int) -> bool | None:
    """True when latest < simple MA of the last `window` observations.
    None when history is shorter than the window (no truncated MA)."""
    if len(values) < window:
        return None
    ma = sum(values[-window:]) / window
    return values[-1] < ma


def grade(measure: Decimal | None, yellow: Decimal | None,
          red: Decimal | None) -> str:
    """measure >= red → RED, >= yellow → YELLOW, else GREEN. Thresholds
    are oriented so that BIGGER = WORSE (callers negate falls)."""
    if measure is None:
        return NO_DATA
    if red is not None and measure >= red:
        return RED
    if yellow is not None and measure >= yellow:
        return YELLOW
    return GREEN


# --------------------------------------------------------------------------- #
# Config-driven signal evaluation
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SignalResult:
    id: str
    link: str
    label: str
    status: str
    measure: Decimal | None       # the graded quantity (bigger = worse)
    detail: str                   # human-readable reading
    as_of: date | None = None


def _d(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _fmt_pct(x: Decimal | None) -> str:
    return "n/a" if x is None else f"{(x * 100).quantize(Decimal('0.1'))}%"


def _fmt(x: Decimal | None, places: str = "0.01") -> str:
    return "n/a" if x is None else str(x.quantize(Decimal(places)))


def evaluate_signal(
    spec: Mapping,
    series: Mapping[str, Sequence[Decimal]],
    as_of: Mapping[str, date | None] | None = None,
) -> SignalResult:
    """Evaluate one config signal against named ascending series.

    kinds:
      widening      series, lookback, yellow, red       (latest − trailing min)
      level         series, yellow, red                 (latest level)
      fall_pct      series, lookback, yellow, red       (decline over lookback, as +fraction)
      rel_return    series, vs, lookback, yellow, red   (underperformance vs `vs`, as +fraction)
      drawdown      series, window, yellow, red         (fraction below trailing high)
      basket_drawdown  members, window, yellow, red     (mean drawdown across members present)
      below_ma      series, window, [red_drawdown]      (YELLOW below MA; RED if also ≥ red_drawdown off high)
      divergence    spread, spread_lookback, spread_widening, equity, equity_window, near_high
                    (RED when credit widens while equity sits near its high; else GREEN)
    """
    sid, link, label, kind = spec["id"], spec["link"], spec.get("label", spec["id"]), spec["kind"]
    y, r = _d(spec.get("yellow")), _d(spec.get("red"))
    as_of = as_of or {}

    def get(name: str) -> Sequence[Decimal]:
        return series.get(name) or []

    def result(status, measure, detail, key=None) -> SignalResult:
        return SignalResult(sid, link, label, status, measure, detail,
                            as_of.get(key) if key else None)

    if kind == "widening":
        s = get(spec["series"])
        m = widening(s, int(spec["lookback"]))
        return result(grade(m, y, r), m,
                      f"{_fmt(s[-1] if s else None)} ({_fmt(m)} off {spec['lookback']}-obs low)",
                      spec["series"])
    if kind == "level":
        s = get(spec["series"])
        m = s[-1] if s else None
        return result(grade(m, y, r), m, f"{_fmt(m)}", spec["series"])
    if kind == "fall_pct":
        s = get(spec["series"])
        ch = pct_change(s, int(spec["lookback"]))
        m = None if ch is None else -ch
        return result(grade(m, y, r), m,
                      f"{_fmt_pct(ch)} over {spec['lookback']} obs", spec["series"])
    if kind == "rel_return":
        a, b = get(spec["series"]), get(spec["vs"])
        lb = int(spec["lookback"])
        ra, rb = trailing_return(a, lb), trailing_return(b, lb)
        m = None if ra is None or rb is None else rb - ra
        return result(grade(m, y, r), m,
                      f"{spec['series']} {_fmt_pct(ra)} vs {spec['vs']} {_fmt_pct(rb)} ({lb} sessions)",
                      spec["series"])
    if kind == "drawdown":
        s = get(spec["series"])
        m = drawdown_from_high(s, int(spec["window"]))
        return result(grade(m, y, r), m,
                      f"{_fmt_pct(m)} below {spec['window']}-session high", spec["series"])
    if kind == "basket_drawdown":
        dds = {name: drawdown_from_high(get(name), int(spec["window"]))
               for name in spec["members"]}
        present = {k: v for k, v in dds.items() if v is not None}
        m = (sum(present.values()) / len(present)) if present else None
        worst = max(present, key=present.get) if present else None
        detail = (f"mean {_fmt_pct(m)} below {spec['window']}-session highs "
                  f"({len(present)}/{len(dds)} names; worst {worst} {_fmt_pct(present.get(worst))})"
                  if present else f"0/{len(dds)} names")
        return result(grade(m, y, r), m, detail,
                      next(iter(present), None))
    if kind == "below_ma":
        s = get(spec["series"])
        w = int(spec["window"])
        below = below_moving_average(s, w)
        if below is None:
            return result(NO_DATA, None, f"<{w} sessions of history", spec["series"])
        dd = drawdown_from_high(s, 252)
        red_dd = _d(spec.get("red_drawdown"))
        if below and red_dd is not None and dd is not None and dd >= red_dd:
            status = RED
        else:
            status = YELLOW if below else GREEN
        return result(status, dd,
                      f"{'below' if below else 'above'} {w}-day MA; {_fmt_pct(dd)} off 1y high",
                      spec["series"])
    if kind == "divergence":
        sp, eq = get(spec["spread"]), get(spec["equity"])
        wid = widening(sp, int(spec["spread_lookback"]))
        dd = drawdown_from_high(eq, int(spec["equity_window"]))
        if wid is None or dd is None:
            return result(NO_DATA, None, "insufficient history", spec["spread"])
        diverging = wid >= _d(spec["spread_widening"]) and dd <= _d(spec["near_high"])
        return result(RED if diverging else GREEN, wid,
                      f"{spec['spread']} +{_fmt(wid)} off low while {spec['equity']} "
                      f"{_fmt_pct(dd)} below high", spec["spread"])
    raise ValueError(f"unknown signal kind {kind!r} for {sid}")


# --------------------------------------------------------------------------- #
# Links and stage
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class HumanScore:
    link: str
    status: str
    note: str
    recorded_on: date


@dataclass(frozen=True)
class LinkStatus:
    link: str
    name: str
    status: str                       # effective (worst of auto + live human)
    auto: str                         # worst automated signal, or NO_DATA
    human: str | None                 # human score if present and fresh
    human_note: str | None
    human_stale: bool                 # a score exists but is older than max age
    human_recorded_on: date | None
    signals: tuple[SignalResult, ...] = field(default_factory=tuple)


def link_statuses(
    signals: Sequence[SignalResult],
    human: Mapping[str, HumanScore],
    *, today: date, human_max_age_days: int,
) -> list[LinkStatus]:
    out = []
    for code, name in LINKS.items():
        sigs = tuple(s for s in signals if s.link == code)
        auto = _worst([s.status for s in sigs])
        h = human.get(code)
        stale = h is not None and (today - h.recorded_on).days > human_max_age_days
        live = h.status if h is not None and not stale else None
        out.append(LinkStatus(
            link=code, name=name,
            status=_worst([auto] + ([live] if live else [])),
            auto=auto, human=live, human_note=h.note if h else None,
            human_stale=stale, human_recorded_on=h.recorded_on if h else None,
            signals=sigs,
        ))
    return out


@dataclass(frozen=True)
class StageResult:
    stage: int
    red_links: tuple[str, ...]
    hard_reds: tuple[str, ...]
    market_confirmed: bool
    reasons: tuple[str, ...]


def stage(
    links: Sequence[LinkStatus],
    hard_reds: Sequence[str],
    signals: Sequence[SignalResult],
    *, confirm_signal_ids: Sequence[str],
    credit_links: Sequence[str] = ("A", "D"),
    capex_link: str = "C",
) -> StageResult:
    """Pre-committed staging rule (plan §3):
      0  ≤1 red link and no hard red
      1  2 red links, or any hard red
      2  ≥3 red links, or (a credit link AND the capex link) both red
      3  stage-2 conditions AND market confirmation (every confirm signal ≥ YELLOW)
    Reds count once per link. A hard red alone reaches stage 1, never 2+.
    """
    red = tuple(l.link for l in links if l.status == RED)
    by_id = {s.id: s for s in signals}
    confirm = bool(confirm_signal_ids) and all(
        sid in by_id and _SEVERITY[by_id[sid].status] >= _SEVERITY[YELLOW]
        for sid in confirm_signal_ids)
    reasons = []
    credit_and_capex = capex_link in red and any(c in red for c in credit_links)
    if len(red) >= 3 or credit_and_capex:
        lvl = 2
        reasons.append(f"{len(red)} red links" if len(red) >= 3
                       else "credit and capex links both red")
        if confirm:
            lvl = 3
            reasons.append("market confirmation: " + ", ".join(confirm_signal_ids))
        if hard_reds:
            reasons.append("hard red: " + ", ".join(hard_reds))
    elif len(red) == 2 or hard_reds:
        lvl = 1
        if len(red) == 2:
            reasons.append("2 red links")
        if hard_reds:
            reasons.append("hard red: " + ", ".join(hard_reds))
    else:
        lvl = 0
        reasons.append(f"{len(red)} red link(s), no hard red")
    return StageResult(lvl, red, tuple(hard_reds), confirm, tuple(reasons))
