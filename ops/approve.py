"""The invariant-6 human approval gate (CLI). No order reaches the engine

without passing through here — the sole exception is the documented
FORCED_LIQUIDATION quarantine-timeout path.

Approving a CAUSAL_BREAK liquidation is the CONFIRMATION EVENT of a falsified
milestone: on approval the thesis flips INVALIDATED and its forecasts resolve
deterministically (forecast_resolution_effect). Rejecting reverts the
milestone to PENDING with your rationale (an LLM misread, corrected).

    uv run python -m ops.approve            # interactive queue
    uv run python -m ops.approve --list     # read-only view
"""

import sys
from datetime import datetime, timezone

from analytics.engine.metrics import forecast_resolution_effect
from config.loader import CONFIG
from data import repository as repo
from data.db import connect
from schemas.enums import (
    ExitType,
    ForecastOutcome,
    MilestoneStatus,
    OrderStatus,
    ProvenanceType,
    ThesisStatus,
)
from schemas.models import Assertion, Document, Forecast, Order, ResearchGenealogy, Thesis


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
            lines.append(f"decision packet: {doc.retrieval_url}")
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


def interactive() -> None:
    with connect() as conn:
        queue = pending_orders(conn)
        if not queue:
            print("no PROPOSED orders — queue is clear")
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


if __name__ == "__main__":
    if "--list" in sys.argv:
        with connect() as conn:
            for o in pending_orders(conn):
                print(render_context(conn, o))
    else:
        interactive()
