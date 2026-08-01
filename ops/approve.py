"""The human decision gate (CLI) — every decision in the unified pipeline
lands here (consolidation spec, DECISIONS.md 2026-07-22):

- orders:  the invariant-6 approval gate. No order reaches the engine without
  passing through here — the sole exception is the documented
  FORCED_LIQUIDATION quarantine-timeout path. Approving a CAUSAL_BREAK
  liquidation is the CONFIRMATION EVENT of a falsified milestone; approving a
  TIME_STOP unwind is the confirmation event of a `dead` decision.
- themes:  propose / accept / reject candidates from the unified intake queue.
  HARD CAP: one accepted theme per calendar day (ET), across all sources —
  checked here, enforced by the DB's partial unique index. Reject CLOSES the
  thread (terminal); an UNDECIDED candidate defers at end of day and
  resurfaces Monday — only an explicit reject kills a theme. `amend` applies
  HUMAN research-layer edits (description, per-theme emphasis_terms /
  fred_series, add-only symbols) to an open theme — the channel for external
  research packets (DECISIONS.md "External research packets").
- theses:  accept (REVIEWED → ACTIVE + sized proposals) or reject (thread
  fully closed) a Friday deep-dive thesis.
- dead:    the ONLY path that retires a thesis by human judgment. Proposes
  TIME_STOP unwinds for open positions (forecasts → UNRESOLVED, never
  TRUE/FALSE — invariant 7); with no positions it closes immediately.

    uv run python -m ops.approve                      # interactive order queue
    uv run python -m ops.approve --list               # read-only view
    uv run python -m ops.approve theme list
    uv run python -m ops.approve theme propose <slug> --title ... --symbols A,B
    uv run python -m ops.approve theme accept <slug> [--rationale ...]
    uv run python -m ops.approve theme reject <slug> --rationale ...
    uv run python -m ops.approve theme amend <slug> [--description-file f]
        [--add-symbols A,B] [--emphasis-terms ...] [--fred-series ...]
        --rationale ...
    uv run python -m ops.approve thesis accept <id> [--rationale ...]
    uv run python -m ops.approve thesis reject <id> --rationale ...
    uv run python -m ops.approve dead <thesis_id> --rationale ...
"""

import argparse
import sys
import textwrap
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from analytics.engine import clock
from analytics.engine.metrics import forecast_resolution_effect
from config.loader import CONFIG
from data import repository as repo
from data.db import connect
from schemas.enums import (
    ExitType,
    ForecastOutcome,
    MilestoneStatus,
    OrderSide,
    OrderStatus,
    ProvenanceType,
    ThemeSource,
    ThemeStatus,
    ThesisStatus,
)
from schemas.models import (
    Assertion,
    Document,
    Forecast,
    Order,
    ResearchGenealogy,
    Theme,
    ThemeSymbol,
    Thesis,
)

ET = ZoneInfo("America/New_York")


def today_et() -> date:
    return datetime.now(ET).date()


def pending_orders(conn) -> list[Order]:
    return repo.fetch(conn, Order, "status = 'PROPOSED' ORDER BY id")


def render_context(conn, order: Order) -> str:
    lines = [
        f"\n{'=' * 70}",
        f"ORDER #{order.id}: {order.side.value} {order.proposed_qty} {order.symbol}"
        f"  [{order.strategy_tag}]"
        + (f"  EXIT: {order.exit_type.value}" if order.exit_type else ""),
        f"proposed {order.proposed_at:%Y-%m-%d %H:%M} UTC",
    ]
    thesis = repo.fetch_one(conn, Thesis, "id = %s", (order.thesis_id,))
    if thesis:
        lines.append(f"thesis v{thesis.version} ({thesis.status.value}): {thesis.title}")
        if thesis.do_nothing_case:
            lines.append(f"do-nothing case: {thesis.do_nothing_case[:300]}")
    if order.decision_packet_doc_id:
        doc = repo.fetch_one(conn, Document, "id = %s", (order.decision_packet_doc_id,))
        if doc:
            lines.append(
                f"decision packet: documents #{doc.id} —"
                f" `ops.approve packet {doc.id}` to view"
            )
    incidents = conn.execute(
        "SELECT incident_class, symbol, left(description, 80) FROM data_incidents"
        " WHERE status = 'OPEN' ORDER BY id DESC LIMIT 5"
    ).fetchall()
    if incidents:
        lines.append("OPEN INCIDENTS:")
        lines.extend(f"  {c} {s}: {d}" for c, s, d in incidents)
    tainted = conn.execute(
        "SELECT count(*) FROM daily_nav WHERE experiment_id = %s"
        " AND contains_forced_resolution", (order.experiment_id,),
    ).fetchone()[0]
    if tainted:
        lines.append(f"⚠ NAV series contains {tainted} forced-resolution session(s)")
    return "\n".join(lines)


def approve_order(conn, order_id: int, qty=None, rationale: str | None = None,
                  commit: bool = True) -> Order:
    order = repo.fetch_one(conn, Order, "id = %s", (order_id,))
    if order.status is not OrderStatus.PROPOSED:
        raise ValueError(f"order {order_id} is {order.status}, not PROPOSED")
    now = datetime.now(timezone.utc)
    approved_qty = qty if qty is not None else order.proposed_qty
    conn.execute(
        "UPDATE orders SET status = 'APPROVED', approved_qty = %s, human_rationale = %s,"
        " decided_at = %s WHERE id = %s",
        (approved_qty, rationale, now, order_id),
    )
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "order_approval", "order_id": order_id,
                   "proposed_qty": str(order.proposed_qty),
                   "approved_qty": str(approved_qty), "modified": qty is not None},
            human_rationale=rationale or "approved as proposed",
            created_at=now,
        ),
    )
    if order.exit_type is ExitType.CAUSAL_BREAK:
        _confirm_causal_break(conn, order, now)
    if order.exit_type is ExitType.TIME_STOP:
        _confirm_time_stop(conn, order, now)
    if commit:
        conn.commit()
    return repo.fetch_one(conn, Order, "id = %s", (order_id,))


def reject_order(conn, order_id: int, rationale: str, commit: bool = True) -> Order:
    order = repo.fetch_one(conn, Order, "id = %s", (order_id,))
    if order.status is not OrderStatus.PROPOSED:
        raise ValueError(f"order {order_id} is {order.status}, not PROPOSED")
    now = datetime.now(timezone.utc)
    conn.execute(
        "UPDATE orders SET status = 'REJECTED', human_rationale = %s, decided_at = %s"
        " WHERE id = %s", (rationale, now, order_id),
    )
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "order_rejection", "order_id": order_id},
            human_rationale=rationale, created_at=now,
        ),
    )
    if order.exit_type is ExitType.CAUSAL_BREAK:
        _revert_milestone(conn, order, rationale, now)
    if order.exit_type is ExitType.TIME_STOP:
        _revert_dead(conn, order, rationale, now)
    if commit:
        conn.commit()
    return repo.fetch_one(conn, Order, "id = %s", (order_id,))


def _confirm_causal_break(conn, order: Order, now: datetime) -> None:
    """Human approval = confirmation event: thesis INVALIDATED, forecasts

    resolve per the invariant-7 authority (CAUSAL_BREAK → FALSE)."""
    cause = forecast_resolution_effect(ExitType.CAUSAL_BREAK)
    conn.execute(
        "UPDATE theses SET status = 'INVALIDATED' WHERE id = %s"
        " AND status = 'ACTIVE'", (order.thesis_id,),
    )
    for f in repo.fetch(
        conn, Forecast, "thesis_id = %s AND outcome IS NULL", (order.thesis_id,)
    ):
        conn.execute(
            "UPDATE forecasts SET outcome = 'FALSE', resolution_cause = %s::resolution_cause,"
            " resolved_at = %s, brier_score = %s WHERE id = %s",
            (cause.value, now, (f.probability - 0.0) ** 2, f.id),
        )


def _revert_milestone(conn, order: Order, rationale: str, now: datetime) -> None:
    conn.execute(
        "UPDATE milestones SET status = 'PENDING', status_changed_at = %s"
        " WHERE thesis_id = %s AND status = 'FALSIFIED'", (now, order.thesis_id),
    )
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "milestone_revert", "thesis_id": order.thesis_id},
            human_rationale=f"CAUSAL_BREAK rejected: {rationale}", created_at=now,
        ),
    )
    # Cancel sibling CAUSAL_BREAK proposals for the same thesis
    conn.execute(
        "UPDATE orders SET status = 'CANCELLED', decided_at = %s WHERE thesis_id = %s"
        " AND exit_type = 'CAUSAL_BREAK' AND status = 'PROPOSED'",
        (now, order.thesis_id),
    )


def kill_thesis(conn, thesis_id: int, rationale: str, commit: bool = True) -> None:
    """All proposals rejected + human wants the thesis dead → genealogy."""
    now = datetime.now(timezone.utc)
    thesis = repo.fetch_one(conn, Thesis, "id = %s", (thesis_id,))
    conn.execute("UPDATE theses SET status = 'REJECTED' WHERE id = %s", (thesis_id,))
    repo.insert(
        conn,
        ResearchGenealogy(
            experiment_id=thesis.experiment_id,
            hypothesis_title=thesis.title,
            thesis_id=thesis_id,
            rejected=True,
            rejection_reason=f"human at approval gate: {rationale}",
            variant_count=thesis.version,
            evidence_added_after_price_observation=False,
            created_at=now,
        ),
    )
    if commit:
        conn.commit()


# --------------------------------------------------------------------------- #
# Theme intake decisions (unified pipeline, migration 004)
# --------------------------------------------------------------------------- #

class ThemeGateError(Exception):
    pass


def _theme(conn, slug: str) -> Theme:
    theme = repo.fetch_one(conn, Theme, "slug = %s", (slug,))
    if theme is None:
        raise ThemeGateError(f"no theme with slug {slug!r} — see `theme list`")
    return theme


def accept_slot_used(conn, day: date) -> str | None:
    """The slug that holds `day`'s single acceptance slot, if any."""
    row = conn.execute(
        "SELECT slug FROM themes WHERE accepted_on = %s", (day,)
    ).fetchone()
    return row[0] if row else None


def propose_theme(
    conn, slug: str, title: str, symbols: list[str],
    description: str | None = None, source: ThemeSource = ThemeSource.HUMAN,
    source_ref: str = "human", research_config_extra: dict | None = None,
    commit: bool = True,
) -> Theme:
    now = datetime.now(timezone.utc)
    theme = Theme(
        slug=slug, title=title, description=description, source=source,
        status=ThemeStatus.CANDIDATE,
        research_config={"symbols": symbols, **(research_config_extra or {})},
        source_ref=source_ref, proposed_on=today_et(), queued_on=today_et(),
        proposed_at=now, created_at=now,
    )
    repo.insert(conn, theme)
    if commit:
        conn.commit()
    return theme


def accept_theme(conn, slug: str, rationale: str | None = None,
                 commit: bool = True) -> Theme:
    """Take today's single acceptance slot. Symbols activate at the next
    session, so ingestion has their bars before the first gate that needs
    them; the theme joins this week's Friday deep-dive set."""
    theme = _theme(conn, slug)
    if theme.status not in (ThemeStatus.CANDIDATE, ThemeStatus.DEFERRED):
        raise ThemeGateError(f"theme {slug} is {theme.status}, not decidable")
    today = today_et()
    holder = accept_slot_used(conn, today)
    if holder is not None:
        raise ThemeGateError(
            f"today's acceptance slot is already used by {holder!r} — "
            f"one accepted theme per day, across all sources (spec invariant 1)"
        )
    now = datetime.now(timezone.utc)
    activation = clock.first_session_open_after(now)
    conn.execute(
        "UPDATE themes SET status = 'ACCEPTED', accepted_on = %s, decided_at = %s,"
        " resurface_on = NULL WHERE id = %s",
        (today, now, theme.id),
    )
    for symbol in theme.research_config.get("symbols", []):
        conn.execute(
            "INSERT INTO theme_symbols (theme_id, symbol, activation_session, created_at)"
            " VALUES (%s, %s, %s, %s) ON CONFLICT (theme_id, symbol) DO NOTHING",
            (theme.id, symbol, activation, now),
        )
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "theme_accept", "theme_id": theme.id, "slug": slug,
                   "accepted_on": str(today), "activation_session": str(activation)},
            human_rationale=rationale or "accepted",
            created_at=now,
        ),
    )
    if commit:
        conn.commit()
    return repo.fetch_one(conn, Theme, "id = %s", (theme.id,))


def _next_monday(day: date) -> date:
    return day + timedelta(days=(7 - day.weekday()) % 7 or 7)


def reject_theme(conn, slug: str, rationale: str, commit: bool = True) -> Theme:
    """An explicit reject is TERMINAL: the thread closes and only a fresh
    proposal re-creates the theme. (Undecided candidates are the ones that
    roll forward — see roll_theme_queue.)"""
    theme = _theme(conn, slug)
    if theme.status not in (ThemeStatus.CANDIDATE, ThemeStatus.DEFERRED):
        raise ThemeGateError(f"theme {slug} is {theme.status}, not decidable")
    now = datetime.now(timezone.utc)
    conn.execute(
        "UPDATE themes SET status = 'CLOSED', closed_reason = 'user_rejected',"
        " decided_at = %s, resurface_on = NULL WHERE id = %s",
        (now, theme.id),
    )
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "theme_reject", "theme_id": theme.id, "slug": slug},
            human_rationale=rationale, created_at=now,
        ),
    )
    if commit:
        conn.commit()
    return repo.fetch_one(conn, Theme, "id = %s", (theme.id,))


def amend_theme(
    conn, slug: str, rationale: str,
    description: str | None = None,
    add_symbols: list[str] | None = None,
    emphasis_terms: list[str] | None = None,
    fred_series: list[str] | None = None,
    commit: bool = True,
) -> tuple[Theme, list[str]]:
    """HUMAN amendment of an open theme's research layer (DECISIONS.md
    "External research packets"). Amendable fields: description, per-theme
    emphasis_terms / fred_series (each REPLACES the per-theme list; an empty
    theme falls back to the config slice defaults), and add-only symbols.

    Guardrails:
    - a CLOSED theme is not amendable — propose a fresh theme instead;
    - a theme with an ACTIVE thesis is not amendable — the monitoring context
      is part of what the human accepted; retire via `dead` first;
    - symbols are add-only, activating next session (point-in-time membership
      never restates history — removal is not a concept the ledger supports).

    Returns (refreshed theme, list of changed-field labels).
    """
    theme = _theme(conn, slug)
    if theme.status is ThemeStatus.CLOSED:
        raise ThemeGateError(f"theme {slug} is CLOSED — propose a fresh theme")
    active = conn.execute(
        "SELECT id FROM theses WHERE theme_id = %s AND status = 'ACTIVE'",
        (theme.id,),
    ).fetchone()
    if active is not None:
        raise ThemeGateError(
            f"theme {slug} has ACTIVE thesis #{active[0]} — amend would change"
            f" a live monitoring context; retire it via `dead` first"
        )

    now = datetime.now(timezone.utc)
    config = dict(theme.research_config)
    changed: list[str] = []
    audit: dict = {"kind": "theme_amend", "theme_id": theme.id, "slug": slug}

    if description is not None and description != theme.description:
        audit["description_before"] = theme.description
        changed.append("description")
    else:
        description = theme.description
    if emphasis_terms is not None:
        audit["emphasis_terms_before"] = config.get("emphasis_terms")
        config["emphasis_terms"] = emphasis_terms
        changed.append("emphasis_terms")
    if fred_series is not None:
        audit["fred_series_before"] = config.get("fred_series")
        config["fred_series"] = fred_series
        changed.append("fred_series")

    added = []
    if add_symbols:
        known = set(config.get("symbols", []))
        added = [s.strip().upper() for s in add_symbols if s.strip()]
        added = [s for s in dict.fromkeys(added) if s not in known]
    if added:
        config["symbols"] = config.get("symbols", []) + added
        changed.append(f"symbols +{','.join(added)}")
        if theme.status is ThemeStatus.ACCEPTED:
            # same activation rule as accept_theme: bars exist before the
            # first gate that needs them; pre-activation history never restates
            activation = clock.first_session_open_after(now)
            for symbol in added:
                conn.execute(
                    "INSERT INTO theme_symbols (theme_id, symbol,"
                    " activation_session, created_at) VALUES (%s, %s, %s, %s)"
                    " ON CONFLICT (theme_id, symbol) DO NOTHING",
                    (theme.id, symbol, activation, now),
                )
        # on CANDIDATE/DEFERRED themes accept_theme activates them later

    if not changed:
        raise ThemeGateError("nothing to amend — no field differs")

    conn.execute(
        "UPDATE themes SET description = %s, research_config = %s WHERE id = %s",
        (description, Json(config), theme.id),
    )
    audit["changed"] = changed
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN, value=audit,
            human_rationale=rationale, created_at=now,
        ),
    )
    if commit:
        conn.commit()
    return repo.fetch_one(conn, Theme, "id = %s", (theme.id,)), changed


def roll_theme_queue(conn, session: date, commit: bool = True) -> tuple[int, int]:
    """Nightly queue mechanics (called from the daily monitor's intake phase).

    1. Due DEFERRED themes re-enter the queue (Mondays) with a fresh
       queued_on, so tonight's defer pass leaves them alone.
    2. Candidates the human passed over (queued before this session, still
       undecided) DEFER and resurface next Monday. Deciding nothing never
       kills a theme; only an explicit reject does.

    Returns (resurfaced, deferred).
    """
    resurfaced = conn.execute(
        "UPDATE themes SET status = 'CANDIDATE', queued_on = %s, resurface_on = NULL"
        " WHERE status = 'DEFERRED' AND resurface_on <= %s",
        (session, session),
    ).rowcount
    deferred = conn.execute(
        "UPDATE themes SET status = 'DEFERRED', resurface_on = %s,"
        " rolled_forward_count = rolled_forward_count + 1"
        " WHERE status = 'CANDIDATE' AND queued_on < %s",
        (_next_monday(session), session),
    ).rowcount
    if commit:
        conn.commit()
    return resurfaced, deferred


def _close_theme(conn, theme_id: int | None, reason: str, now: datetime) -> None:
    if theme_id is None:
        return
    conn.execute(
        "UPDATE themes SET status = 'CLOSED', closed_reason = %s, decided_at = %s"
        " WHERE id = %s AND status != 'CLOSED'", (reason, now, theme_id),
    )


# --------------------------------------------------------------------------- #
# Thesis decisions (REVIEWED → user gate) and the `dead` path
# --------------------------------------------------------------------------- #

def _thesis_packet_doc_id(conn, thesis: Thesis) -> int | None:
    """Newest cached decision packet for this thesis's theme — the evidence
    the order gate shows must be the packet of THIS deep dive, not whichever
    theme happened to run last."""
    from reporting import packet

    return packet.latest_doc_id(conn, thesis.strategy_tag)


def packet_text(conn, doc_id: int) -> str:
    """The exact evidence a documents row holds (invariant 6 viewing path:
    packets are DB-only; this is how the human reads what an order cites)."""
    doc = repo.fetch_one(
        conn, Document, "id = %s AND source_name = 'desk_packet'", (doc_id,)
    )
    if doc is None:
        raise ThemeGateError(f"documents #{doc_id} is not a cached decision packet")
    return doc.extracted_text or doc.raw_bytes.decode("utf-8", errors="replace")


def accept_thesis(conn, thesis_id: int, rationale: str | None = None,
                  commit: bool = True) -> list[Order]:
    """REVIEWED → ACTIVE, then sized proposals immediately (they still stop at
    the order gate). Returns the proposed orders."""
    from ops.proposal_builder import build_proposals

    thesis = repo.fetch_one(conn, Thesis, "id = %s", (thesis_id,))
    if thesis is None or thesis.status is not ThesisStatus.REVIEWED:
        status = thesis.status if thesis else "missing"
        raise ThemeGateError(f"thesis {thesis_id} is {status}, not REVIEWED")
    now = datetime.now(timezone.utc)
    conn.execute("UPDATE theses SET status = 'ACTIVE' WHERE id = %s", (thesis_id,))
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "thesis_accept", "thesis_id": thesis_id},
            human_rationale=rationale or "accepted", created_at=now,
        ),
    )
    thesis = repo.fetch_one(conn, Thesis, "id = %s", (thesis_id,))
    orders = build_proposals(
        conn, thesis, today_et(), _thesis_packet_doc_id(conn, thesis), commit=False
    )
    if commit:
        conn.commit()
    return orders


def reject_thesis(conn, thesis_id: int, rationale: str, commit: bool = True) -> None:
    """Thread fully closed: thesis REJECTED (genealogy row) + theme CLOSED.
    The theme only comes back if a source proposes it again as a new row."""
    thesis = repo.fetch_one(conn, Thesis, "id = %s", (thesis_id,))
    if thesis is None or thesis.status is not ThesisStatus.REVIEWED:
        status = thesis.status if thesis else "missing"
        raise ThemeGateError(f"thesis {thesis_id} is {status}, not REVIEWED")
    kill_thesis(conn, thesis_id, rationale, commit=False)
    _close_theme(conn, thesis.theme_id, "thesis_rejected", datetime.now(timezone.utc))
    if commit:
        conn.commit()


def mark_dead(conn, thesis_id: int, rationale: str, commit: bool = True) -> list[Order]:
    """The ONLY human path that retires a live thesis (spec invariant 4: dead
    is always a user decision; the system never auto-unwinds).

    Open positions → TIME_STOP SELL proposals; approving them at the order
    gate is the confirmation event (thesis CLOSED, forecasts UNRESOLVED —
    invariant 7: a manual exit never scores a forecast). No open positions →
    the thesis closes immediately. Returns the proposed unwind orders.
    """
    thesis = repo.fetch_one(conn, Thesis, "id = %s", (thesis_id,))
    if thesis is None or thesis.status is not ThesisStatus.ACTIVE:
        status = thesis.status if thesis else "missing"
        raise ThemeGateError(f"thesis {thesis_id} is {status}, not ACTIVE")
    now = datetime.now(timezone.utc)
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "thesis_dead", "thesis_id": thesis_id},
            human_rationale=rationale, created_at=now,
        ),
    )
    open_positions = conn.execute(
        "SELECT DISTINCT ON (symbol) symbol, qty FROM positions"
        " WHERE experiment_id = %s ORDER BY symbol, as_of DESC",
        (thesis.experiment_id,),
    ).fetchall()
    held = {
        s: q for s, q in open_positions
        if q > 0 and conn.execute(
            "SELECT 1 FROM trades WHERE symbol = %s AND strategy_tag = %s LIMIT 1",
            (s, thesis.strategy_tag),
        ).fetchone()
    }
    orders: list[Order] = []
    for symbol, qty in sorted(held.items()):
        order = Order(
            experiment_id=thesis.experiment_id, thesis_id=thesis_id,
            symbol=symbol, side=OrderSide.SELL, status=OrderStatus.PROPOSED,
            strategy_tag=thesis.strategy_tag, proposed_qty=qty,
            exit_type=ExitType.TIME_STOP, proposed_at=now, created_at=now,
        )
        repo.insert(conn, order)
        orders.append(order)
    if not orders:
        _finalize_dead(conn, thesis, now)
    if commit:
        conn.commit()
    return orders


def _finalize_dead(conn, thesis: Thesis, now: datetime) -> None:
    conn.execute(
        "UPDATE theses SET status = 'CLOSED' WHERE id = %s AND status = 'ACTIVE'",
        (thesis.id,),
    )
    for f in repo.fetch(
        conn, Forecast, "thesis_id = %s AND outcome IS NULL", (thesis.id,)
    ):
        conn.execute(
            "UPDATE forecasts SET outcome = 'UNRESOLVED', resolution_cause = 'MANUAL',"
            " resolved_at = %s WHERE id = %s", (now, f.id),
        )
    _close_theme(conn, thesis.theme_id, "dead", now)


def _confirm_time_stop(conn, order: Order, now: datetime) -> None:
    """Approving a TIME_STOP unwind confirms the `dead` decision."""
    thesis = repo.fetch_one(conn, Thesis, "id = %s", (order.thesis_id,))
    if thesis is not None:
        _finalize_dead(conn, thesis, now)


def _revert_dead(conn, order: Order, rationale: str, now: datetime) -> None:
    """Rejecting the unwind cancels the death: sibling TIME_STOP proposals
    cancelled, thesis stays ACTIVE and keeps monitoring."""
    conn.execute(
        "UPDATE orders SET status = 'CANCELLED', decided_at = %s WHERE thesis_id = %s"
        " AND exit_type = 'TIME_STOP' AND status = 'PROPOSED'",
        (now, order.thesis_id),
    )
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "dead_reverted", "thesis_id": order.thesis_id},
            human_rationale=f"unwind rejected: {rationale}", created_at=now,
        ),
    )


# --------------------------------------------------------------------------- #
# Queue rendering
# --------------------------------------------------------------------------- #

def theme_queue(conn) -> list[Theme]:
    return repo.fetch(
        conn, Theme,
        "status IN ('CANDIDATE', 'DEFERRED') ORDER BY status, proposed_on, id",
    )


def reviewed_theses(conn) -> list[Thesis]:
    return repo.fetch(conn, Thesis, "status = 'REVIEWED' ORDER BY id")


def render_theme_queue(conn) -> str:
    today = today_et()
    holder = accept_slot_used(conn, today)
    lines = [
        f"today's acceptance slot ({today}): "
        + (f"USED by {holder!r}" if holder else "OPEN"),
    ]
    for t in theme_queue(conn):
        age = (today - t.proposed_on).days
        extra = (f", resurfaces {t.resurface_on}" if t.status is ThemeStatus.DEFERRED
                 else "")
        rolled = f", rolled forward x{t.rolled_forward_count}" if t.rolled_forward_count else ""
        lines.append(
            f"  [{t.status.value}] {t.slug} ({t.source.value.lower()}, {age}d old"
            f"{rolled}{extra}): {t.title}"
        )
        if t.description:
            for para in t.description.strip().split("\n\n"):
                lines += [
                    textwrap.indent(
                        textwrap.fill(ln, width=88, subsequent_indent="  "), "      "
                    )
                    for ln in para.splitlines()
                ]
                lines.append("")
    if len(lines) == 1:
        lines.append("  (queue empty)")
    pending = reviewed_theses(conn)
    if pending:
        lines.append("theses awaiting your decision:")
        lines.extend(
            f"  thesis #{t.id} [{t.strategy_tag}]: {t.title}" for t in pending
        )
    return "\n".join(lines)


def interactive() -> None:
    with connect() as conn:
        print(render_theme_queue(conn))
        queue = pending_orders(conn)
        if not queue:
            print("no PROPOSED orders — order queue is clear")
            return
        print(f"{len(queue)} order(s) awaiting approval")
        for order in queue:
            print(render_context(conn, order))
            while True:
                choice = input("[a]pprove / [m]odify qty / [r]eject / [s]kip > ").strip().lower()
                if choice == "a":
                    approve_order(conn, order.id, rationale=input("rationale (optional) > ") or None)
                    print("APPROVED — fills at the next session open")
                    break
                if choice == "m":
                    from decimal import Decimal
                    qty = Decimal(input("new qty > ").strip())
                    approve_order(conn, order.id, qty=qty,
                                  rationale=input("rationale (required) > "))
                    print(f"APPROVED at {qty}")
                    break
                if choice == "r":
                    reject_order(conn, order.id, rationale=input("rationale (required) > "))
                    print("REJECTED")
                    break
                if choice == "s":
                    break


def pin_restatement(conn, symbol: str, rationale: str):
    """Resolve a vendor-restatement REVISION incident (invariant 8). The
    HUMAN bar_pin assertion accepts the newest document going forward;
    replayed history stays on the previously-adopted bars for sessions
    <= through_session via the manifest overlay (persist._apply_bar_pins).
    Fills are historical facts; never restated."""
    from data.price_bars import _latest_docs_by_symbol

    symbol = symbol.upper()
    candidate = _latest_docs_by_symbol(conn).get(symbol)
    if candidate is None:
        raise ValueError(f"no tiingo documents for {symbol}")
    row = conn.execute(
        "SELECT source_doc_hash FROM price_bars WHERE symbol = %s LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no persisted bars for {symbol} — nothing was restated")
    adopted = row[0]
    if adopted == candidate.content_hash:
        raise ValueError(f"{symbol}: newest document already adopted — nothing to pin")
    through = conn.execute("SELECT max(nav_date) FROM daily_nav").fetchone()[0]
    if through is None:
        raise ValueError("no replayed sessions yet — nothing to pin against")
    now = datetime.now(timezone.utc)
    aid = repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "bar_pin", "symbol": symbol,
                   "doc_hash_old": adopted,
                   "doc_hash_new": candidate.content_hash,
                   "through_session": through.isoformat()},
            human_rationale=rationale,
            created_at=now,
        ),
    )
    resolved = conn.execute(
        "UPDATE data_incidents SET status = 'RESOLVED_HUMAN', resolved_at = %s,"
        " resolution_assertion_id = %s WHERE symbol = %s AND status = 'OPEN'"
        " AND incident_class = 'REVISION'",
        (now, aid, symbol),
    ).rowcount
    return aid, resolved, candidate.content_hash, through


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ops.approve", description=__doc__)
    parser.add_argument("--list", action="store_true", help="read-only order view")
    sub = parser.add_subparsers(dest="command")

    theme = sub.add_parser("theme", help="theme intake decisions").add_subparsers(
        dest="theme_command", required=True
    )
    theme.add_parser("list", help="show the candidate queue + today's slot")
    p = theme.add_parser("propose", help="user-sourced candidate theme")
    p.add_argument("slug")
    p.add_argument("--title", required=True)
    p.add_argument("--symbols", required=True, help="comma-separated tickers")
    p.add_argument("--description", default=None)
    p = theme.add_parser("accept", help="take today's single acceptance slot")
    p.add_argument("slug")
    p.add_argument("--rationale", default=None)
    p = theme.add_parser("reject", help="close the thread (terminal; undecided themes defer on their own)")
    p.add_argument("slug")
    p.add_argument("--rationale", required=True)
    p = theme.add_parser(
        "amend",
        help="HUMAN amendment of an open theme's research layer"
             " (description / per-theme config / add-only symbols)",
    )
    p.add_argument("slug")
    p.add_argument("--description", default=None)
    p.add_argument("--description-file", default=None,
                   help="read the new description from a file (wins over --description)")
    p.add_argument("--add-symbols", default=None,
                   help="comma-separated tickers to ADD (removal unsupported —"
                        " point-in-time membership)")
    p.add_argument("--emphasis-terms", default=None,
                   help="comma-separated; REPLACES the per-theme emphasis term list")
    p.add_argument("--fred-series", default=None,
                   help="comma-separated FRED series ids; REPLACES the per-theme list")
    p.add_argument("--rationale", required=True)

    idea = sub.add_parser(
        "idea", help="queue a research idea for the nightly scout to develop"
    ).add_subparsers(dest="idea_command", required=True)
    p = idea.add_parser("add", help="queue an idea (FIFO; oldest consumed first)")
    p.add_argument("text", help="the idea seed, quoted — a hypothesis sketch,"
                   " not a full proposal")
    idea.add_parser("list", help="pending seeds + recent outcomes")
    p = idea.add_parser("drop", help="cancel a pending seed")
    p.add_argument("seed_id", type=int)
    p.add_argument("--rationale", required=True)

    thesis = sub.add_parser("thesis", help="deep-dive thesis decisions").add_subparsers(
        dest="thesis_command", required=True
    )
    p = thesis.add_parser("accept", help="REVIEWED → ACTIVE + sized proposals")
    p.add_argument("thesis_id", type=int)
    p.add_argument("--rationale", default=None)
    p = thesis.add_parser("reject", help="close the thread (theme CLOSED)")
    p.add_argument("thesis_id", type=int)
    p.add_argument("--rationale", required=True)

    p = sub.add_parser("dead", help="retire a live thesis; unwind positions")
    p.add_argument("thesis_id", type=int)
    p.add_argument("--rationale", required=True)

    p = sub.add_parser(
        "packet", help="print a cached decision packet (immutable order evidence)"
    )
    p.add_argument("doc_id", type=int)
    p.add_argument("--out", default=None, help="write to a file instead of stdout")

    p = sub.add_parser(
        "pin", help="resolve a vendor restatement: adopt the newest document;"
        " replay keeps the previously-adopted bars <= through_session"
    )
    p.add_argument("symbol")
    p.add_argument("--rationale", required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command is None:
        if args.list:
            with connect() as conn:
                print(render_theme_queue(conn))
                for o in pending_orders(conn):
                    print(render_context(conn, o))
        else:
            interactive()
        return

    with connect() as conn:
        try:
            if args.command == "theme":
                if args.theme_command == "list":
                    print(render_theme_queue(conn))
                elif args.theme_command == "propose":
                    t = propose_theme(
                        conn, args.slug, args.title,
                        [s.strip().upper() for s in args.symbols.split(",") if s.strip()],
                        description=args.description,
                    )
                    print(f"CANDIDATE {t.slug} queued (source: human)")
                elif args.theme_command == "accept":
                    t = accept_theme(conn, args.slug, args.rationale)
                    print(
                        f"ACCEPTED {t.slug} — takes {t.accepted_on}'s slot; "
                        f"deep dive runs Friday night"
                    )
                elif args.theme_command == "reject":
                    t = reject_theme(conn, args.slug, args.rationale)
                    print(f"REJECTED {t.slug} — thread closed; only a fresh "
                          f"proposal brings it back")
                elif args.theme_command == "amend":
                    description = args.description
                    if args.description_file:
                        from pathlib import Path

                        description = Path(args.description_file).read_text()
                    split = lambda raw: (  # noqa: E731
                        [s.strip() for s in raw.split(",") if s.strip()]
                        if raw is not None else None
                    )
                    t, changed = amend_theme(
                        conn, args.slug, args.rationale,
                        description=description,
                        add_symbols=split(args.add_symbols),
                        emphasis_terms=split(args.emphasis_terms),
                        fred_series=split(args.fred_series),
                    )
                    print(f"AMENDED {t.slug}: {'; '.join(changed)}"
                          f" (HUMAN assertion logged)")
            elif args.command == "idea":
                from research import idea_seeds
                if args.idea_command == "add":
                    seed_id = idea_seeds.queue_seed(conn, args.text)
                    print(
                        f"SEED #{seed_id} queued — the next nightly scout"
                        f" develops it (replaces that night's organic funnel)"
                    )
                elif args.idea_command == "list":
                    rows = idea_seeds.list_seeds(conn)
                    if not rows:
                        print("no idea seeds")
                    for sid, status, text, created, consumed_on, outcome, slug in rows:
                        line = f"  #{sid} [{status}] ({created.date()}) {text[:110]}"
                        if outcome:
                            line += f" → {outcome} {consumed_on}"
                            if slug:
                                line += f" ({slug})"
                        print(line)
                elif args.idea_command == "drop":
                    try:
                        idea_seeds.cancel_seed(conn, args.seed_id, args.rationale)
                    except ValueError as e:
                        raise ThemeGateError(str(e)) from e
                    print(f"CANCELLED seed #{args.seed_id}")
            elif args.command == "thesis":
                if args.thesis_command == "accept":
                    orders = accept_thesis(conn, args.thesis_id, args.rationale)
                    print(
                        f"ACTIVE thesis #{args.thesis_id} — {len(orders)} sized "
                        f"proposal(s) now await order approval (run ops.approve)"
                    )
                else:
                    reject_thesis(conn, args.thesis_id, args.rationale)
                    print(
                        f"REJECTED thesis #{args.thesis_id} — thread closed; the "
                        f"theme only returns via a fresh proposal"
                    )
            elif args.command == "packet":
                text = packet_text(conn, args.doc_id)
                if args.out:
                    from pathlib import Path

                    Path(args.out).write_text(text)
                    print(f"wrote documents #{args.doc_id} to {args.out}")
                else:
                    print(text)
            elif args.command == "pin":
                aid, resolved, new_hash, through = pin_restatement(
                    conn, args.symbol, args.rationale
                )
                print(
                    f"PINNED {args.symbol.upper()}: assertion A{aid} adopts doc "
                    f"{new_hash[:12]}; replay keeps adopted bars through {through}; "
                    f"{resolved} REVISION incident(s) resolved"
                )
            elif args.command == "dead":
                orders = mark_dead(conn, args.thesis_id, args.rationale)
                if orders:
                    print(
                        f"DEAD pending confirmation: {len(orders)} TIME_STOP unwind "
                        f"proposal(s) await order approval — approving them closes "
                        f"thesis #{args.thesis_id}"
                    )
                else:
                    print(f"DEAD confirmed: thesis #{args.thesis_id} closed "
                          f"(no open positions); off monitoring")
        except ThemeGateError as e:
            print(f"refused: {e}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
