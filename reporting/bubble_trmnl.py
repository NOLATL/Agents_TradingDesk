"""AI-bubble risk board for the TRMNL e-ink display: merge variables plus the
webhook push, same pattern as Agents_Cronometer. Operator-only (invariant 5).
Dormant until BUBBLE_TRMNL_UUID is set. Markup: reporting/trmnl/bubble.liquid.html."""

import json
import os
from decimal import Decimal

import httpx

from analytics.bubble_monitor import GREEN, NO_DATA, RED, YELLOW
from reporting.bubble_monitor import (
    ACTION_TEXT,
    STAGE_TEXT,
    BubbleReport,
    _value_at,
    score_delta,
)

WEBHOOK = "https://trmnl.com/api/custom_plugins/{uuid}"
MAX_BYTES = 1900                # stays inside the free-tier limit the health board uses
HIST_BARS = 60
SHORT_NAMES = {"A": "Funding", "B": "Money into AI", "C": "Big-tech capex", "D": "Financing",
               "E": "Physical build", "F": "Compute/semis", "G": "AI revenue",
               "H": "Accounting", "I": "Market"}
STATUS_TEXT = {RED: "RED", YELLOW: "YEL", GREEN: "OK", NO_DATA: "--"}


def _signed(x: Decimal | None) -> str:
    if x is None:
        return "n/a"
    rounded = round(float(x))
    return f"{rounded:+d}" if rounded else "0"


def _hist(report: BubbleReport) -> list[int]:
    """Score history as bar heights, scaled to its own range: on a 0-100 axis
    a 45→68 climb looks flat, and the trend is the point of this chart."""
    values = [p.score for p in report.history if p.score is not None]
    if len(values) > HIST_BARS:
        step = len(values) / HIST_BARS
        values = [values[int(i * step)] for i in range(HIST_BARS - 1)] + [values[-1]]
    if not values:
        return []
    lo, span = min(values), (max(values) - min(values)) or Decimal(1)
    return [int(10 + (v - lo) / span * 90) for v in values]


def merge_vars(r: BubbleReport) -> dict:
    prior = _value_at(r.history, 30)
    links = []
    for l in r.links:
        sc = r.score.links.get(l.link)
        then = prior.links.get(l.link) if prior else None
        links.append({
            "n": SHORT_NAMES[l.link],
            "s": 0 if sc is None else int(sc),
            "t": "--" if sc is None else f"{sc:.0f}",
            "d": "" if sc is None or then is None else _signed(sc - then),
            "x": STATUS_TEXT[l.status],
            "h": bool(l.human) and l.auto == NO_DATA,      # hatched = your rating, not a feed
        })
    signals = [s for l in r.links for s in l.signals if s.severity is not None]
    top = sorted(signals, key=lambda s: s.severity, reverse=True)[:3]
    trend = [int(p.score) for p in r.history if p.score is not None]
    unscored = [SHORT_NAMES[l.link] for l in r.links if l.status == NO_DATA]
    gap = ""
    if r.gaps:
        gap = f"FEED GAPS: {len(r.gaps)}. Check the desk."
    elif unscored:
        gap = "Unrated: " + ", ".join(unscored)
    return {
        "d": r.session.strftime("%b %-d"),
        "s": 0 if r.score.score is None else int(r.score.score),
        "b": r.score.band or "NO DATA",
        "cov": int(r.score.coverage * 100),
        "st": r.stage,
        "stn": STAGE_TEXT[r.stage],
        "act": ACTION_TEXT[r.stage],
        "pend": (f"Rule reads stage {r.result.stage}. Not in effect until it holds."
                 if r.result.stage != r.stage else ""),
        "w1": _signed(score_delta(r.history, 7)),
        "m1": _signed(score_delta(r.history, 30)),
        "m3": _signed(score_delta(r.history, 91)),
        "hist": _hist(r),
        "lo": min(trend, default=0),
        "hi": max(trend, default=0),
        "links": links,
        "drv": [f"{s.severity:.0f} {s.label}"[:44] for s in top],
        "gap": gap,
    }


def encode(mv: dict) -> bytes:
    body = json.dumps({"merge_variables": mv, "merge_strategy": "replace"},
                      separators=(",", ":"), ensure_ascii=True).encode()
    if len(body) > MAX_BYTES:
        raise ValueError(f"TRMNL payload {len(body)} B exceeds {MAX_BYTES} B")
    return body


def push(body: bytes) -> str | None:
    """Gap note on failure, else None. Dormant with no UUID: a screen is never
    worth failing the nightly run over."""
    uuid = os.environ.get("BUBBLE_TRMNL_UUID", "")
    if not uuid:
        return None
    try:
        httpx.post(WEBHOOK.format(uuid=uuid), content=body,
                   headers={"Content-Type": "application/json"}, timeout=30).raise_for_status()
        return None
    except httpx.HTTPError as e:
        return f"TRMNL push failed ({type(e).__name__})"
