"""Deterministic prechecks for scout proposals (2026-07-29 anti-hallucination
work order). These run locally before each Sonnet review attempt: fabricated
tickers, non-US listings, and broken evidence citations are caught by Python
and fed back to the local generator without spending the audit budget.

Checks validate structure and existence only — semantic judgment (does the
evidence support the claim?) stays with the review gate. Every precheck call
leaves a scout_attempts row (migration 007), the measurement instrument for
the rejection-reduction readout.
"""

import re
from datetime import date, datetime, timezone

from data.adapters import tiingo

CITATION_RE = re.compile(r"\[([HE]\d+)\]")
RISING_WORDS = re.compile(
    r"\b(ris|rising|rose|surg|grow|growing|growth|accelerat|increas|climb|spike|soar)",
    re.IGNORECASE,
)


def check_symbols(conn, symbols: list[str]) -> list[str]:
    """Fabricated-ticker and US-only violations. Empty directory → no checks
    (skip, never all-unknown)."""
    rows = tiingo.lookup_symbols(conn, symbols)
    if not rows:
        return []
    issues = []
    for symbol in symbols:
        row = rows.get(symbol.upper())
        if row is None:
            issues.append(
                f"Ticker {symbol.upper()} does not exist in the symbol directory —"
                " it is not a real, tradeable US ticker. Replace it with a correct"
                " US-listed symbol from the evidence, or drop it and note the gap."
            )
        elif (row["exchange"] or "") not in tiingo.US_EXCHANGES or row["asset_type"] != "Stock":
            issues.append(
                f"Ticker {symbol.upper()}"
                + (f" ({row['name']})" if row["name"] else "")
                + f" is listed as {row['asset_type'] or 'unknown type'} on"
                f" {row['exchange'] or 'an unknown exchange'} — the desk trades US"
                " major-exchange stocks only (NYSE/NASDAQ/AMEX; no OTC, no foreign"
                " listings). If the pure-play is foreign-listed or OTC-only, drop"
                " it and note the gap instead of substituting a guess."
            )
    return issues


def check_citations(evidence_points: list[str], issued_ids: set[str]) -> list[str]:
    """Every evidence point must cite at least one issued evidence ID; cited
    IDs must exist; the proposal needs >=2 distinct IDs overall."""
    issues = []
    cited_all: set[str] = set()
    for i, point in enumerate(evidence_points, 1):
        cited = set(CITATION_RE.findall(point))
        unknown = cited - issued_ids
        if not cited:
            issues.append(
                f"Evidence point {i} cites no evidence ID — every point must"
                " begin with the bracketed IDs it rests on, e.g. [H4][E2]."
            )
        if unknown:
            issues.append(
                f"Evidence point {i} cites {', '.join(sorted(unknown))} which"
                " does not exist in the context — only IDs actually listed may"
                " be cited."
            )
        cited_all |= cited & issued_ids
    if len(cited_all) < 2:
        issues.append(
            "The proposal must rest on at least two DISTINCT cited evidence"
            f" IDs; it currently cites {len(cited_all)}."
        )
    return issues


def check_directions(evidence_points: list[str], description: str,
                     edgar_directions: dict[str, str]) -> list[str]:
    """A DECLINED EDGAR term cited alongside rising language is (almost
    always) the misread-trend failure. Deterministic best-effort; the review
    gate remains the semantic backstop."""
    issues = []
    declined = {i for i, d in edgar_directions.items() if d == "DECLINED"}
    for text in [*evidence_points, description]:
        for eid in set(CITATION_RE.findall(text)) & declined:
            if RISING_WORDS.search(text):
                issues.append(
                    f"You cite [{eid}], whose EDGAR trend DECLINED, in a"
                    " sentence with rising/growth language. A declining trend"
                    " is contrary evidence — characterize it as declining (or"
                    " drop it), never as corroboration."
                )
    return issues


def record_attempt(conn, session: date, stage: str, passed: bool,
                   issues: str | None, context_chars: int,
                   model_tag: str) -> None:
    """One instrument row per precheck/review event. Does not commit — the
    caller owns the transaction (the intake rejection path commits these
    rows explicitly so the instrument survives a rejected night)."""
    attempt_no = conn.execute(
        "SELECT count(*) + 1 FROM scout_attempts"
        " WHERE session_date = %s AND stage = %s", (session, stage),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO scout_attempts (session_date, attempt_no, stage, passed,"
        " issues, context_chars, model_tag, recorded_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (session, attempt_no, stage, passed, issues, context_chars,
         model_tag, datetime.now(timezone.utc)),
    )


def precheck_proposal(conn, proposal, issued_ids: set[str],
                      edgar_directions: dict[str, str], session: date,
                      context_chars: int, model_tag: str) -> str | None:
    """Compose all deterministic issues into one corrective message (None =
    clean). Also enriches valid symbols with Tiingo company names so the
    review context can show authoritative identities."""
    symbols = [s.strip().upper() for s in proposal.symbols]
    issues = check_symbols(conn, symbols)
    issues += check_citations(proposal.evidence_points, issued_ids)
    issues += check_directions(proposal.evidence_points, proposal.description,
                               edgar_directions)
    passed = not issues
    message = None
    if issues:
        message = (
            "Deterministic validation found problems (this is not the human"
            " reviewer — fix these and regenerate):\n"
            + "\n".join(f"- {i}" for i in issues)
        )
    record_attempt(conn, session, "PRECHECK", passed, message,
                   context_chars, model_tag)
    if passed:
        tiingo.fill_symbol_meta(conn, symbols)
    return message


def symbol_identity_block(conn, symbols: list[str]) -> str:
    """Authoritative ticker → company lines for the review context, so the
    auditor checks identity against the directory, not parametric memory."""
    rows = tiingo.lookup_symbols(conn, [s.strip().upper() for s in symbols])
    if not rows:
        return ""
    lines = []
    for symbol, row in rows.items():
        if row is None:
            lines.append(f"{symbol}: NOT FOUND in symbol directory")
        else:
            desc = f" — {row['description'][:200]}" if row["description"] else ""
            lines.append(
                f"{symbol} = {row['name'] or '(name unavailable)'}"
                f" ({row['exchange'] or '?'}){desc}"
            )
    return (
        "\n\nSYMBOL DIRECTORY CHECK (authoritative Tiingo identities — reject"
        " any claim that contradicts these):\n"
        + "\n".join(f"- {l}" for l in lines)
    )
