"""Intraday Qwen-only staging watch (2026-08-07 scale-up P3, Jared's design).

Four times a day (07/11/15/19 ET, 7 days/week — systemd timer
`trading-desk-intraday.timer`) a local-only batch ingests fresh RSS, rebuilds
the scout evidence context, runs the idea funnel's generate+gate stages, and
stages the surviving sketches in `idea_sketches` for the NEXT nightly scout
session. The 23:00 scout then re-issues each pending sketch as a fresh [Sn]
context item, so the mandatory nightly proposal draws on the best pattern of
the whole day (weekend batches feed Monday) instead of a 23:00 snapshot.

Zero Anthropic spend: everything here is deterministic Python plus local
Qwen calls. Sketches are operational staging (like idea_seeds) — never
provenance, never a decision input, and nothing publishes without passing
the nightly Sonnet gate (invariant 13 intact). A failed batch never blocks
anything: the nightly scout degrades to its previous single-pass behavior.

    uv run python -m ops.intraday_scout
"""

import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from analytics.engine import clock
from config.loader import SLICE
from data import repository as repo
from data.adapters import rss
from research import idea_funnel, proposal_checks
from schemas.models import Experiment

ET = ZoneInfo("America/New_York")
MAX_STAGED = 8          # [Sn] items offered to the nightly scout
MAX_EVIDENCE_LINES = 4  # per staged sketch, in the rendered context
_ID_MARKER_RE = re.compile(r"^- \[[A-Z]{1,2}\d+\] ")


def batch_key(now: datetime) -> str:
    """Idempotency bucket: the ET date+hour of the scheduled slot (the
    timer's RandomizedDelaySec stays inside the hour)."""
    return now.astimezone(ET).strftime("%Y-%m-%dT%H")


def target_session(now: datetime) -> date:
    """The XNYS session whose 23:00 scout consumes this batch: today if
    today is a session (all batch slots fire before 23:00 ET), else the
    next session — so weekend and holiday batches feed Monday's proposal.
    exchange_calendars, never weekday math."""
    day = now.astimezone(ET).date()
    for _ in range(10):
        if clock.is_session(day):
            return day
        day += timedelta(days=1)
    raise RuntimeError(f"no XNYS session within 10 days of {day}")


def _clean_line(line: str) -> str:
    """Strip the batch-local citation marker ('- [H4] ...') — IDs do not
    survive across context builds; the nightly scout re-issues [Sn]."""
    return _ID_MARKER_RE.sub("", line).lstrip("- ").strip()


def _dedup_key(idea) -> str:
    """Normalized domain+title slug — the cross-batch dedup. Best-effort:
    a re-phrased title stages again and the nightly gate sees near-dupes,
    which it already handles."""
    slug = re.sub(r"[^a-z0-9]+", "-", idea.working_title.lower()).strip("-")
    domain = re.sub(r"[^a-z0-9]+", "", (idea.domain or "").lower())
    return f"{domain}:{slug}"[:120]


def run_batch(conn, now: datetime | None = None) -> dict:
    """One staging pass: fresh RSS -> scout context (+ variety pressure from
    already-staged titles) -> funnel generate + gate -> insert survivors.
    Instrument rows land in scout_attempts as IDEA/IDEA_GATE tagged with the
    batch key, so the coverage-balance hint sees intraday domains too."""
    from research import candidate_scout  # lazy: candidate_scout imports us

    now = now or datetime.now(timezone.utc)
    session = target_session(now)
    key = batch_key(now)
    rss.ingest_all()
    exp = repo.fetch_one(conn, Experiment, "name = 'EXP-001'")
    ctx = candidate_scout.build_context(conn)
    model_tag = SLICE["llm"].get("scout_model_tag", SLICE["llm"]["model_tag"])

    pending = conn.execute(
        "SELECT working_title FROM idea_sketches"
        " WHERE session_date = %s AND status = 'PENDING' ORDER BY id",
        (session,),
    ).fetchall()
    if pending:
        ctx.text += (
            "\n\nALREADY STAGED FOR TONIGHT'S SCOUT (produce genuinely"
            " DIFFERENT ideas — these are queued already):\n"
            + "\n".join(f"- {t}" for (t,) in pending)
        )

    slate = idea_funnel.generate_ideas(
        conn, experiment_id=exp.id, ctx=ctx, model_tag=model_tag)
    proposal_checks.record_attempt(
        conn, session, "IDEA", True,
        f"[intraday {key}] "
        + "; ".join(f"{i.working_title} [{i.domain}]" for i in slate.ideas),
        len(ctx.text), model_tag)
    ranked, summary = idea_funnel.gate_ideas(
        conn, experiment_id=exp.id, ctx=ctx, slate=slate, model_tag=model_tag)
    proposal_checks.record_attempt(
        conn, session, "IDEA_GATE", bool(ranked),
        f"[intraday {key}] {summary}", len(ctx.text), model_tag)

    inserted = 0
    for rank, idea in enumerate(ranked, 1):
        ids = idea_funnel.valid_ids(idea, ctx.issued_ids)
        evidence = {
            "ids": ids,
            "lines": [_clean_line(ctx.evidence_lines[i])
                      for i in ids if i in ctx.evidence_lines],
            "links": [ctx.headline_items[i].get("link")
                      for i in ids
                      if i in ctx.headline_items
                      and ctx.headline_items[i].get("link")],
        }
        row = conn.execute(
            "INSERT INTO idea_sketches (session_date, batch_key, working_title,"
            " hypothesis, domain, evidence, gate_rank, gate_reason, dedup_key,"
            " model_tag) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (session_date, dedup_key) DO NOTHING RETURNING id",
            (session, key, idea.working_title, idea.hypothesis, idea.domain,
             json.dumps(evidence), rank, idea.non_overlap, _dedup_key(idea),
             model_tag),
        ).fetchone()
        inserted += 1 if row is not None else 0
    # No commit here — the caller owns the transaction (ops.intraday_scout's
    # finish_cycle commits the batch atomically with its cycle_log row).
    return {"session": session, "batch": key, "ideas": len(slate.ideas),
            "kept": len(ranked), "inserted": inserted}


def attach_staged_sketches(conn, ctx, session: date) -> dict[str, int]:
    """23:00 integration: expire stale PENDING sketches, then re-issue this
    session's pending sketches as fresh [Sn] context items (mutating ctx
    text/issued_ids/evidence_lines/headline_items so citation prechecks and
    the funnel's deep-evidence article pulls work unchanged). Returns
    {sketch_id_label: db_id} so the scout can resolve WINNER/PASSED_OVER.
    Pure enrichment — the caller wraps it in try/except."""
    conn.execute(
        "UPDATE idea_sketches SET status = 'EXPIRED'"
        " WHERE status = 'PENDING' AND session_date < %s", (session,))
    rows = conn.execute(
        "SELECT id, working_title, hypothesis, domain, evidence, batch_key"
        " FROM idea_sketches WHERE session_date = %s AND status = 'PENDING'"
        " ORDER BY COALESCE(gate_rank, 99), id", (session,),
    ).fetchall()
    sid_map: dict[str, int] = {}
    lines = []
    for i, (dbid, title, hypothesis, domain, evidence, batch) in enumerate(
            rows[:MAX_STAGED], 1):
        sid = f"S{i}"
        sid_map[sid] = dbid
        ev = "; ".join((evidence or {}).get("lines", [])[:MAX_EVIDENCE_LINES])
        line = (f"- [{sid}] (staged {batch}) {title}"
                + (f" [{domain}]" if domain else "") + f": {hypothesis}"
                + (f"\n    evidence: {ev}" if ev else ""))
        lines.append(line)
        ctx.issued_ids.add(sid)
        ctx.evidence_lines[sid] = line
        links = (evidence or {}).get("links") or []
        ctx.headline_items[sid] = {"title": title, "summary": hypothesis,
                                   "link": links[0] if links else None,
                                   "body": None}
    if lines:
        ctx.text += (
            "\n\nSTAGED INTRADAY IDEAS (the desk's watch accumulated these"
            " since the last session — treat them as first-class candidate"
            " patterns alongside tonight's evidence; cite their [Sn] IDs):\n"
            + "\n".join(lines)
        )
    return sid_map


def resolve_sketches(conn, sid_map: dict[str, int], cited_ids: set[str]) -> None:
    """Mark sketches the published proposal cited as WINNER, the rest
    PASSED_OVER. Rides the caller's transaction (intake commits). A
    rejected/errored night leaves them PENDING; next session's attach pass
    expires them."""
    if not sid_map:
        return
    winners = [dbid for sid, dbid in sid_map.items() if sid in cited_ids]
    others = [dbid for sid, dbid in sid_map.items() if sid not in cited_ids]
    if winners:
        conn.execute("UPDATE idea_sketches SET status = 'WINNER'"
                     " WHERE id = ANY(%s)", (winners,))
    if others:
        conn.execute("UPDATE idea_sketches SET status = 'PASSED_OVER'"
                     " WHERE id = ANY(%s)", (others,))
