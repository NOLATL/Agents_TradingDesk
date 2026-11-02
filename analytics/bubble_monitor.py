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
- score 0-100: weighted severity across scored links; a gauge and trend. The
  stage rule alone sets the action.
"""

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal

from analytics.momentum import QUARANTINE_KEY

__all__ = [
    "QUARANTINE_KEY", "GREEN", "YELLOW", "RED", "NO_DATA", "LINKS",
    "SignalResult", "LinkStatus", "StageResult",
    "widening", "pct_change", "drawdown_from_high", "trailing_return",
    "below_moving_average", "grade", "severity", "evaluate_signal",
    "link_statuses", "stage", "link_score", "composite", "ScoreResult",
    "next_escalation", "discrete_quarters", "derive_fundamentals",
    "ReplayPoint", "evaluate_at", "replay", "held_stages",
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


def severity(measure: Decimal | None, calm: Decimal, yellow: Decimal,
             red: Decimal) -> Decimal | None:
    """Piecewise-linear 0-100: calm→0, yellow→50, red→85, then on to 100."""
    if measure is None:
        return None
    if measure <= calm:
        return Decimal(0)
    if measure < yellow:
        return 50 * (measure - calm) / (yellow - calm)
    if measure < red:
        return 50 + 35 * (measure - yellow) / (red - yellow)
    return min(Decimal(100), 85 + 15 * (measure - red) / (red - yellow))


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
    severity: Decimal | None = None   # 0-100


def _d(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _fmt_pct(x: Decimal | None) -> str:
    return "n/a" if x is None else f"{(x * 100).quantize(Decimal('0.1'))}%"


def _fmt(x: Decimal | None, places: str = "0.01") -> str:
    return "n/a" if x is None else str(x.quantize(Decimal(places)))


_DISCRETE_SEVERITY = {GREEN: Decimal(10), YELLOW: Decimal(50), RED: Decimal(85)}


def evaluate_signal(
    spec: Mapping,
    series: Mapping[str, Sequence[Decimal]],
    as_of: Mapping[str, date | None] | None = None,
) -> SignalResult:
    """Grade one config signal and attach its 0-100 severity. Kinds with
    thresholds scale continuously; below_ma/divergence are discrete."""
    res = _grade_signal(spec, series, as_of)
    y, r = _d(spec.get("yellow")), _d(spec.get("red"))
    cap = spec.get("max_status")            # build-up gauges: raise the score, never break
    if cap and _SEVERITY[res.status] > _SEVERITY[cap]:
        res = replace(res, status=cap)
    if res.status == NO_DATA:
        sev = None
    elif y is not None and r is not None:
        calm = _d(spec.get("calm"))
        if calm is None:
            calm = min(Decimal(0), 2 * y - r)
        sev = severity(res.measure, calm, y, r)
    else:
        sev = _DISCRETE_SEVERITY[res.status]
    return replace(res, severity=sev)


def _grade_signal(
    spec: Mapping,
    series: Mapping[str, Sequence[Decimal]],
    as_of: Mapping[str, date | None] | None = None,
) -> SignalResult:
    """Evaluate one config signal against named ascending series.

    kinds:
      widening      series, lookback, yellow, red       (latest − trailing min)
      level         series, yellow, red                 (latest level)
      below         series, yellow, red                 (latest level, low = bad; thresholds negated)
      fall_pct      series, lookback, yellow, red       (decline over lookback, as +fraction)
      fall_pts      series, window, yellow, red         (trailing max − latest, same units)
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
    if kind == "below":
        s = get(spec["series"])
        m = -s[-1] if s else None
        return result(grade(m, y, r), m, _fmt_pct(s[-1] if s else None), spec["series"])
    if kind == "fall_pct":
        s = get(spec["series"])
        ch = pct_change(s, int(spec["lookback"]))
        m = None if ch is None else -ch
        return result(grade(m, y, r), m,
                      f"{_fmt_pct(ch)} over {spec['lookback']} obs", spec["series"])
    if kind == "fall_pts":
        s = get(spec["series"])
        window = s[-int(spec["window"]):]
        m = max(window) - s[-1] if s else None
        return result(grade(m, y, r), m,
                      f"{_fmt(s[-1] if s else None)} ({_fmt(m)} off {spec['window']}-obs high)",
                      spec["series"])
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


def held_stages(raw: Sequence[int], *, confirm_up: int, confirm_down: int,
                floors: Sequence[int] | None = None) -> list[int]:
    """Debounce the rule: a new stage takes effect only after the raw stage
    has stayed at or above it for `confirm_up` sessions, and steps down only
    after `confirm_down` sessions below. floors[i] (active hard reds) applies
    immediately; a hard red never waits."""
    out, cur = [], 0
    for i in range(len(raw)):
        up = min(raw[max(0, i - confirm_up + 1):i + 1]) if i + 1 >= confirm_up else 0
        down = (max(raw[i - confirm_down + 1:i + 1]) if i + 1 >= confirm_down else cur)
        if up > cur:
            cur = up
        elif down < cur:
            cur = down
        cur = max(cur, floors[i] if floors else 0)
        out.append(cur)
    return out


# --------------------------------------------------------------------------- #
# Score (gauge; the stage rule above sets the action)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ScoreResult:
    score: Decimal | None
    band: str | None
    coverage: Decimal                 # share of link weight that was scored
    links: dict[str, Decimal]         # scored links only


def link_score(link: LinkStatus, cfg: Mapping) -> Decimal | None:
    """auto = max_weight*worst + (1-max_weight)*mean of signal severities;
    the link scores the higher of auto and a fresh human score."""
    sevs = [s.severity for s in link.signals if s.severity is not None]
    auto = None
    if sevs:
        w = Decimal(cfg["max_weight"])
        auto = w * max(sevs) + (1 - w) * (sum(sevs) / len(sevs))
    human = Decimal(cfg["human_severity"][link.human]) if link.human else None
    present = [x for x in (auto, human) if x is not None]
    return max(present) if present else None


def composite(links: Sequence[LinkStatus], hard_red_count: int, cfg: Mapping) -> ScoreResult:
    weights = {k: Decimal(v) for k, v in cfg["weights"].items()}
    scored = {l.link: x for l in links if (x := link_score(l, cfg)) is not None}
    used = sum(weights[k] for k in scored)
    total = sum(weights.values())
    if not used:
        return ScoreResult(None, None, Decimal(0), {})
    score = sum(weights[k] * v for k, v in scored.items()) / used
    if hard_red_count:
        floor = Decimal(cfg["hard_red_floor"]) + Decimal(cfg["hard_red_step"]) * (hard_red_count - 1)
        score = max(score, min(floor, Decimal(100)))
    band = next((name for name, upper in cfg["bands"] if score <= Decimal(upper)),
                cfg["bands"][-1][0])
    return ScoreResult(score, band, used / total, scored)


def next_escalation(result: StageResult, links: Sequence[LinkStatus], cfg: Mapping) -> str:
    """Plain statement of what moves the raw rule up, naming the closest links."""
    scores = {l.link: link_score(l, cfg) for l in links}
    closest = sorted(((v, k) for k, v in scores.items()
                      if v is not None and k not in result.red_links), reverse=True)[:2]
    near = ", ".join(f"{k} ({v:.0f})" for v, k in closest) or "none scored"
    if result.stage == 0:
        need = "1 more red link" if len(result.red_links) == 1 else "2 red links"
        return f"Stage 1 needs {need} or any hard red. Closest to red: {near}."
    if result.stage == 1:
        return f"Stage 2 needs 3 red links, or a credit link (A/D) and C both red. Closest: {near}."
    if result.stage == 2:
        return "Stage 3 needs the market to confirm: S&P below its 200-day and HY spreads widening."
    return "At the final stage. Re-entry needs reds to clear for 2 consecutive reviews."


# --------------------------------------------------------------------------- #
# Fundamentals from XBRL facts (pure)
# --------------------------------------------------------------------------- #

Dated = list[tuple[date, Decimal]]


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def discrete_quarters(facts: Sequence[Mapping]) -> dict[date, tuple[Decimal, date]]:
    """{quarter end: (value, first filed)} from duration facts. A ~91-day fact
    is taken directly; otherwise a YTD/annual fact is differenced against the
    prior fact with the same start. Quarters with no such pair are absent."""
    spans: dict[tuple[date, date], tuple[Decimal, str, date]] = {}
    for f in facts:
        if not f.get("start"):
            continue
        key = (_day(f["start"]), _day(f["end"]))
        filed = _day(f["filed"])
        prior = spans.get(key)
        first = min(filed, prior[2]) if prior else filed
        if prior is None or f["filed"] >= prior[1]:      # newest value wins (restated)
            spans[key] = (Decimal(str(f["val"])), f["filed"], first)
        else:
            spans[key] = (prior[0], prior[1], first)
    out: dict[date, tuple[Decimal, date]] = {}
    by_start: dict[date, list[date]] = {}
    for start, end in spans:
        by_start.setdefault(start, []).append(end)
    for (start, end), (val, _, filed) in spans.items():
        days = (end - start).days
        if 80 <= days <= 100:
            out[end] = (val, filed)
    for (start, end), (val, _, filed) in spans.items():
        if (end - start).days <= 100 or end in out:
            continue
        earlier = [e for e in by_start[start] if e < end]
        if not earlier:
            continue
        prev = max(earlier)
        if 80 <= (end - prev).days <= 100:
            out[end] = (val - spans[(start, prev)][0], filed)
    return out


def _bucket(end: date) -> date:
    """Calendar quarter containing `end` (Oracle's Aug-31 → Sep bucket)."""
    q = (end.month + 2) // 3
    return date(end.year, q * 3, 1)


def _by_bucket(quarters: Mapping[date, tuple[Decimal, date]]) -> dict[date, tuple[Decimal, date]]:
    return {_bucket(end): v for end, v in quarters.items()}


def _prev_bucket(b: date, n: int) -> date:
    idx = b.year * 4 + b.month // 3 - 1 - n
    return date(idx // 4, (idx % 4 + 1) * 3, 1)


def _ttm(q: Mapping[date, tuple[Decimal, date]], b: date) -> Decimal | None:
    window = [q.get(_prev_bucket(b, i)) for i in range(4)]
    return None if None in window else sum(v[0] for v in window)


def derive_fundamentals(
    facts: Mapping[str, Mapping[str, Sequence[Mapping]]],
    hyperscalers: Sequence[str], rpo_names: Sequence[str],
) -> dict[str, Dated]:
    """Derived series dated by FILED date, so replay stays point-in-time.
    A bucket counts only once every hyperscaler has reported it."""
    quarters = {sym: {m: _by_bucket(discrete_quarters(f)) for m, f in metrics.items()
                      if m in ("capex", "ocf", "revenue", "dep")}
                for sym, metrics in facts.items()}
    out: dict[str, Dated] = {k: [] for k in
                             ("hs_capex_ocf", "hs_capex_rev", "hs_capex_yoy", "hs_fcf_neg",
                              "hs_capex_dep", "rpo_yoy", "nvda_dso", "nvda_rev_yoy")}
    hs = [quarters.get(sym, {}) for sym in hyperscalers]
    capex_buckets = [set(q.get("capex", {})) for q in hs]
    for b in sorted(set.intersection(*capex_buckets) if capex_buckets else ()):
        filed = max(q["capex"][b][1] for q in hs)
        capex = [_ttm(q["capex"], b) for q in hs]
        ocf = [_ttm(q.get("ocf", {}), b) for q in hs]
        rev = [_ttm(q.get("revenue", {}), b) for q in hs]
        dep = [_ttm(q.get("dep", {}), b) for q in hs]
        if None not in capex and None not in ocf:
            out["hs_capex_ocf"].append((filed, sum(capex) / sum(ocf)))
            out["hs_fcf_neg"].append((filed, Decimal(sum(c > o for c, o in zip(capex, ocf)))))
        if None not in capex and None not in rev:
            out["hs_capex_rev"].append((filed, sum(capex) / sum(rev)))
        if None not in capex and None not in dep:
            out["hs_capex_dep"].append((filed, sum(capex) / sum(dep)))
        prior = [q["capex"].get(_prev_bucket(b, 4)) for q in hs]
        if None not in prior:
            now = sum(q["capex"][b][0] for q in hs)
            out["hs_capex_yoy"].append((filed, now / sum(p[0] for p in prior) - 1))

    def instants(sym: str, metric: str) -> dict[date, tuple[Decimal, date]]:
        points: dict[date, tuple[Decimal, str]] = {}
        for f in facts.get(sym, {}).get(metric, []):
            b = _bucket(_day(f["end"]))
            if f.get("start"):
                continue
            if b not in points or f["filed"] < points[b][1]:
                points[b] = (Decimal(str(f["val"])), f["filed"])
        return {b: (v, _day(fd)) for b, (v, fd) in points.items()}

    rpo = [instants(sym, "rpo") for sym in rpo_names]
    for b in sorted(set.intersection(*[set(r) for r in rpo]) if rpo else ()):
        prior_b = _prev_bucket(b, 4)
        if all(prior_b in r for r in rpo):
            now = sum(r[b][0] for r in rpo)
            then = sum(r[prior_b][0] for r in rpo)
            out["rpo_yoy"].append((max(r[b][1] for r in rpo), now / then - 1))

    nvda_rev = quarters.get("NVDA", {}).get("revenue", {})
    ar = instants("NVDA", "ar")
    for b, (rev_q, filed) in sorted(nvda_rev.items()):
        if b in ar and rev_q:
            out["nvda_dso"].append((filed, ar[b][0] / rev_q * 91))
        prior = nvda_rev.get(_prev_bucket(b, 4))
        if prior and prior[0]:
            out["nvda_rev_yoy"].append((filed, rev_q / prior[0] - 1))
    return {k: sorted(v) for k, v in out.items()}


# --------------------------------------------------------------------------- #
# Point-in-time replay (trends from cached series + dated human rows)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ReplayPoint:
    day: date
    score: Decimal | None
    stage: int                            # raw rule output that day
    hard_reds: int
    links: dict[str, Decimal]
    signals: dict[str, Decimal | None]    # signal id → graded measure
    severities: dict[str, Decimal | None]


def evaluate_at(day: date, dated: Mapping[str, Dated],
                specs: Sequence[Mapping]) -> list[SignalResult]:
    series, as_of = {}, {}
    for name, points in dated.items():
        cut = bisect_right(points, day, key=lambda p: p[0])
        series[name] = [p[1] for p in points[:cut]]
        as_of[name] = points[cut - 1][0] if cut else None
    return [evaluate_signal(spec, series, as_of) for spec in specs]


def replay(
    days: Sequence[date], dated: Mapping[str, Dated], specs: Sequence[Mapping],
    human_rows: Sequence[HumanScore | tuple], hard_rows: Sequence[tuple],
    *, score_cfg: Mapping, human_max_age_days: int, confirm_signal_ids: Sequence[str],
) -> list[ReplayPoint]:
    """Recompute signals, links, stage and score as of each day. human_rows
    are ascending HumanScore rows (status CLEARED withdraws); hard_rows are
    (key, raised_on, cleared_on|None). FRED points use the latest vintage."""
    out = []
    for day in days:
        signals = evaluate_at(day, dated, specs)
        human: dict[str, HumanScore] = {}
        for row in human_rows:
            if row.recorded_on > day:
                continue
            if row.status == "CLEARED":
                human.pop(row.link, None)
            else:
                human[row.link] = row
        hard = [k for k, raised, cleared in hard_rows
                if raised <= day and (cleared is None or cleared > day)]
        links = link_statuses(signals, human, today=day,
                              human_max_age_days=human_max_age_days)
        res = stage(links, hard, signals, confirm_signal_ids=confirm_signal_ids)
        sc = composite(links, len(hard), score_cfg)
        out.append(ReplayPoint(day, sc.score, res.stage, len(hard), sc.links,
                               {s.id: s.measure for s in signals},
                               {s.id: s.severity for s in signals}))
    return out
