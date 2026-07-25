"""Deterministic no-new-claims validator for Report Assembler narrative.

Pure functions — no DB, no network, no LLM. The narrator (agents/roles/
reporter.narrate) is a stylist over verified objects; this module is the
mechanical proof that its prose adds nothing: every number must trace to the
source objects (exact restatement or rounding to FEWER significant digits),
every footnote marker must be a real assertion id from this dive, every
ticker must be in the basket, and every section must clear its word floor.
Validation is not verification of the underlying facts — those carry their
own provenance — it is containment of the narrator.
"""

import math
import re
from dataclasses import dataclass, field

# Words per section: (low, high) targets given to the narrator. The guard
# enforces LENGTH_FLOOR × low as the hard minimum; UNDER_LENGTH violations
# re-prompt with an "expand" critique. Small-int/duration/year exemptions
# below are a deliberate strictness trade (a fabricated small count is
# possible but the false-rejection cost dominates; the Sonnet audit covers
# semantics).
SECTION_TARGETS: dict[str, tuple[int, int]] = {
    "lead": (150, 250),
    "thesis_narrative": (600, 900),
    "macro_narrative": (400, 600),
    "company": (300, 500),  # per company narrative
    "valuation_discussion": (300, 500),
    "forecast_rationale": (400, 600),
    "risk_discussion": (300, 500),
    "monitoring_outlook": (200, 400),
}
LENGTH_FLOOR = 0.6

MARKER_RE = re.compile(r"\[\^A(\d+)\]")
NUMBER_RE = re.compile(
    r"[-+]?\$?\d[\d,]*(?:\.\d+)?"
    r"(?:\s*(?:%|percent|bps|k|thousand|m|mm|million|b|bn|billion|trillion|x)\b|%)?",
    re.IGNORECASE,
)
UNIT_FACTORS = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "trillion": 1e12,
}
DURATION_WORDS = re.compile(
    r"^\s*(?:day|days|week|weeks|month|months|quarter|quarters|year|years|"
    r"names?|companies|stocks?|forecasts?|milestones?|segments?|points?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Violation:
    section: str  # "lead" | "thesis_narrative" | ... | "company:NOC"
    kind: str     # UNKNOWN_NUMBER | UNKNOWN_FOOTNOTE | MISSING_FOOTNOTE | FOREIGN_SYMBOL | UNDER_LENGTH
    detail: str


@dataclass(frozen=True)
class Allowlist:
    corpus: str                       # normalized text the narrator saw
    values: tuple[float, ...]         # canonical numeric values
    allowed_ids: frozenset[int]       # assertion ids the narrator may cite
    allowed_symbols: frozenset[str]   # basket + theme universe tickers


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #

def _normalize(text: str) -> str:
    return " ".join(str(text).replace(",", "").replace("$", "").lower().split())


def build_allowlist(ctx) -> Allowlist:
    """`ctx` is an agents.state.NarrativeContext (duck-typed here to keep
    this module dependency-free and unit-testable with plain objects)."""
    corpus_parts: list[str] = []
    values: set[float] = set()
    ids: set[int] = set()
    symbols: set[str] = set()

    for text in (ctx.theme_title, ctx.theme_description, ctx.verdict,
                 ctx.causal_chain, ctx.valuation_check, ctx.do_nothing_case):
        if text:
            corpus_parts.append(text)
    corpus_parts.extend(ctx.challenges or [])

    for r in ctx.basket or []:
        corpus_parts.append(str(r.get("justification", "")))
        symbols.add(str(r.get("symbol", "")).upper())
        ids.update(r.get("supporting_assertion_ids", []))
        if r.get("rank") is not None:
            values.add(float(r["rank"]))
    for c in ctx.claims or []:
        corpus_parts += [str(c.get("claim", "")), str(c.get("quote", ""))]
        ids.add(c["assertion_id"])
        symbols.add(str(c.get("symbol", "")).upper())
        v, unit = c.get("figure_value"), (c.get("figure_unit") or "")
        if v is not None:
            v = float(v)
            values.add(v)
            unit_l = unit.lower()
            if "million" in unit_l:
                values.add(v * 1e6)
            if "billion" in unit_l:
                values.add(v * 1e9)
            if "thousand" in unit_l:
                values.add(v * 1e3)
            if "percent" in unit_l:
                values.add(v / 100)
    for m in ctx.metrics or []:
        ids.add(m["assertion_id"])
        if m.get("symbol"):
            symbols.add(str(m["symbol"]).upper())
        corpus_parts.append(str(m.get("metric", "")))
        if m.get("value") is not None:
            v = float(m["value"])
            values.update({v, v * 100})  # scores quoted as 0.79 or 79%
    for s in ctx.macro or []:
        corpus_parts.append(str(s.get("series_id", "")))
        ids.update(s.get("assertion_ids", []))
        for k in ("latest", "min", "max"):
            if s.get(k) is not None:
                values.add(float(s[k]))
        if s.get("n_obs") is not None:
            values.add(float(s["n_obs"]))
        for d in (s.get("latest_date"),):
            if d:
                corpus_parts.append(str(d))
    for f in ctx.forecasts or []:
        corpus_parts += [str(f.get("statement", "")),
                         str(f.get("resolution_criterion", "")),
                         str(f.get("horizon_end", ""))]
        for k in ("p_prior", "p_final"):
            if f.get(k) is not None:
                p = float(f[k])
                values.update({p, p * 100})
    for m in getattr(ctx, "milestones", None) or []:
        corpus_parts += [str(m.get("description", "")),
                         str(m.get("falsification_criterion", ""))]
    for v in (getattr(ctx, "exit_criteria", None) or {}).values():
        corpus_parts.append(str(v))

    symbols.discard("")
    return Allowlist(
        corpus=_normalize(" ".join(corpus_parts)),
        values=tuple(values),
        allowed_ids=frozenset(ids),
        allowed_symbols=frozenset(symbols),
    )


# --------------------------------------------------------------------------- #
# Marker + word-count helpers
# --------------------------------------------------------------------------- #

def normalize_markers(text: str, allowed_ids: set[int] | frozenset[int]) -> str:
    """Rewrite the marker variants qwen tends to emit — `A123`, `[A123]`,
    `(A123)`, `^A123`, `[^A123]`, and compound groups like `[^A1, ^A2]` or
    `[A1; A2]` — into canonical `[^A123]` markers, but ONLY for ids in the
    allowlist. A formatting transform of already-validated ids, never a
    content edit."""
    def marker(aid: int, original: str) -> str:
        return f"[^A{aid}]" if aid in allowed_ids else original

    def expand_group(m: re.Match) -> str:
        ids = [int(x) for x in re.findall(r"A(\d+)", m.group(0))]
        if ids and all(i in allowed_ids for i in ids):
            return "".join(f"[^A{i}]" for i in ids)
        return m.group(0)

    # compound citation groups first: [^A1, ^A2], [A1; A2], (A1, A2)
    text = re.sub(r"[\[(]\s*(?:\^?A\d+\s*[,;]\s*)+\^?A\d+\s*[\])]",
                  expand_group, text)

    def sub(m: re.Match) -> str:
        return marker(int(m.group(1)), m.group(0))

    text = re.sub(r"\[\^?A(\d+)\]", sub, text)
    text = re.sub(r"\(A(\d+)\)", sub, text)
    text = re.sub(r"(?<!\[)\^A(\d+)\b", sub, text)
    text = re.sub(r"(?<!\[\^)\bA(\d+)\b(?!\])", sub, text)
    # collapse doubled brackets a chained rewrite could produce
    return text.replace("[^[^", "[^").replace("]]", "]")


def body_word_count(text: str) -> int:
    """Words of actual prose — footnote markers excluded."""
    return len(MARKER_RE.sub(" ", text or "").split())


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def _sig_digits(v: float) -> int:
    s = f"{abs(v):.10g}"
    digits = re.sub(r"[^0-9]", "", s).lstrip("0").rstrip("0")
    return max(len(digits), 1)


def _round_sig(v: float, d: int) -> float:
    if v == 0:
        return 0.0
    return round(v, d - 1 - int(math.floor(math.log10(abs(v)))))


def _token_ok(token: str, allow: Allowlist) -> bool:
    raw = token.strip()
    unit = None
    m = re.search(r"(%|percent|bps|k|thousand|m|mm|million|b|bn|billion|trillion|x)\s*$",
                  raw, re.IGNORECASE)
    if m:
        unit = m.group(1).lower()
        raw = raw[: m.start()]
    num_str = raw.replace("$", "").replace(",", "").replace("+", "").strip()
    try:
        v = float(num_str)
    except ValueError:
        return True  # unparseable → not a numeric claim
    # exemptions
    if unit is None and "$" not in token and v == int(v):
        iv = int(abs(v))
        if 1900 <= iv <= 2100:
            return True
        if iv <= 12:
            return True
    # tier 1: digit-substring in corpus (boundary-guarded)
    needle = re.escape(num_str.lstrip("-"))
    if re.search(rf"(?<![\d.]){needle}(?![\d])", allow.corpus):
        return True
    # tier 2: rounding lattice
    d = _sig_digits(v)
    scaled = abs(v) * UNIT_FACTORS.get(unit or "", 1.0)
    candidates = {abs(v), scaled, scaled / 100, scaled * 100,
                  abs(v) * 1e3, abs(v) * 1e6, abs(v) * 1e9}
    if unit in ("%", "percent"):
        candidates.update({abs(v) / 100})
    for a in allow.values:
        if d > _sig_digits(a):
            continue  # narrative may never ADD precision
        ra = _round_sig(abs(a), d)
        if any(math.isclose(ra, _round_sig(c, d), rel_tol=1e-9) for c in candidates):
            return True
    return False


def check_text(
    section: str,
    text: str,
    *,
    allow: Allowlist,
    known_symbols: set[str] | frozenset[str],
    target_key: str | None = None,
    require_marker: bool = True,
    suggest_ids: list[int] | None = None,
) -> list[Violation]:
    """Validate one narrative section. `known_symbols` = every ticker the DB
    has seen (passed by the caller so this module stays pure); a capitalized
    token is flagged only when it IS a known ticker outside this dive's
    universe — plain English words can't collide."""
    violations: list[Violation] = []
    stripped = MARKER_RE.sub(" ", text or "")

    for aid in (int(x) for x in MARKER_RE.findall(text or "")):
        if aid not in allow.allowed_ids:
            violations.append(Violation(
                section, "UNKNOWN_FOOTNOTE",
                f"[^A{aid}] is not an assertion of this dive — remove or"
                " replace with a provided id"))
    if require_marker and not MARKER_RE.search(text or ""):
        ids_hint = ", ".join(f"A{i}" for i in (suggest_ids or [])[:12])
        violations.append(Violation(
            section, "MISSING_FOOTNOTE",
            "no [^A###] citation in this section"
            + (f"; available: {ids_hint}" if ids_hint else "")))

    for m in NUMBER_RE.finditer(stripped):
        tok = m.group(0)
        # digits glued to a letter/caret are part of an identifier (A1419,
        # Q4, ^A12) — not a numeric claim; footnote checks govern ids
        if m.start() > 0 and (stripped[m.start() - 1].isalpha()
                              or stripped[m.start() - 1] == "^"):
            continue
        after = stripped[m.end():]
        bare = tok.replace("$", "").replace(",", "").strip()
        if (bare.isdigit() and int(bare) <= 60 and "$" not in tok
                and DURATION_WORDS.match(after)):
            continue
        if not _token_ok(tok, allow):
            violations.append(Violation(
                section, "UNKNOWN_NUMBER",
                f"'{tok.strip()}' does not trace to any source object —"
                " restate an actual figure or drop it"))

    for sym in set(re.findall(r"\b[A-Z]{1,5}\b", stripped)):
        if sym in known_symbols and sym not in allow.allowed_symbols:
            violations.append(Violation(
                section, "FOREIGN_SYMBOL",
                f"ticker {sym} is outside this dive's basket/universe"))

    if target_key:
        low, _high = SECTION_TARGETS[target_key]
        words = body_word_count(text or "")
        floor = int(low * LENGTH_FLOOR)
        if words < floor:
            uncited = [i for i in (suggest_ids or [])
                       if f"[^A{i}]" not in (text or "")]
            hint = ", ".join(f"A{i}" for i in uncited[:15])
            violations.append(Violation(
                section, "UNDER_LENGTH",
                f"{words} words < minimum {floor} (target {low}–{_high});"
                " expand: deepen the analysis"
                + (f" of uncovered evidence {hint}" if hint else "")))
    return violations


def format_critique(violations: list[Violation]) -> str:
    return "\n".join(f"- [{v.section}] {v.kind}: {v.detail}" for v in violations)
