"""Human idea seeds for the nightly scout (2026-07-31, DECISIONS.md).

The operator queues raw research hypotheses (`ops.approve idea add`); the
next nightly scout consumes the oldest PENDING seed FIFO, skipping the
organic idea funnel and developing the seed instead — drafting, precheck,
and Sonnet review run unchanged, and the result lands in the normal theme
queue. Nightly-only by design: there is no on-demand run path.

Lifecycle: PENDING → CONSUMED (terminal scout outcome, PROPOSED or
REJECTED) or CANCELLED (operator drop). A scout crash (the intake ERROR
path) rolls the consumption back, so the seed stays PENDING and retries
the next night — the rollback IS the retry mechanism. Documented
trade-off: a persistent review-gate infrastructure failure surfaces as a
review rejection and consumes the seed; loudly notified, recoverable with
one `idea add`.

Queueing and cancelling are human research decisions and write HUMAN
assertions (invariant 1); the table itself is operational queue metadata
like scout_outcomes, never a decision input.
"""

from datetime import date, datetime, timezone

from data import repository as repo
from schemas.enums import ProvenanceType
from schemas.models import Assertion


def queue_seed(conn, text: str, commit: bool = True) -> int:
    text = text.strip()
    if not text:
        raise ValueError("empty idea seed")
    now = datetime.now(timezone.utc)
    seed_id = conn.execute(
        "INSERT INTO idea_seeds (seed_text, created_at) VALUES (%s, %s)"
        " RETURNING id",
        (text, now),
    ).fetchone()[0]
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "idea_seed_queued", "seed_id": seed_id,
                   "seed_text": text[:500]},
            human_rationale="queued for the nightly scout",
            created_at=now,
        ),
    )
    if commit:
        conn.commit()
    return seed_id


def cancel_seed(conn, seed_id: int, rationale: str, commit: bool = True) -> None:
    now = datetime.now(timezone.utc)
    count = conn.execute(
        "UPDATE idea_seeds SET status = 'CANCELLED', cancelled_at = %s"
        " WHERE id = %s AND status = 'PENDING'",
        (now, seed_id),
    ).rowcount
    if count == 0:
        raise ValueError(f"seed #{seed_id} is not PENDING (or does not exist)")
    repo.insert_assertion(
        conn,
        Assertion(
            type=ProvenanceType.HUMAN,
            value={"kind": "idea_seed_cancelled", "seed_id": seed_id},
            human_rationale=rationale,
            created_at=now,
        ),
    )
    if commit:
        conn.commit()


def oldest_pending(conn) -> tuple[int, str] | None:
    """FIFO head of the queue, or None. Pure SELECT — consumption is a
    separate terminal-time write (mark_consumed) so a scout crash never
    burns a seed."""
    row = conn.execute(
        "SELECT id, seed_text FROM idea_seeds WHERE status = 'PENDING'"
        " ORDER BY created_at, id LIMIT 1"
    ).fetchone()
    return (row[0], row[1]) if row else None


def mark_consumed(conn, seed_id: int, session: date, outcome: str,
                  theme_slug: str | None = None,
                  detail: str | None = None) -> None:
    """Terminal write. Does not commit — the caller owns the transaction
    (same contract as proposal_checks.record_attempt): the intake PROPOSED
    path commits it with the theme, the rejection path commits it with the
    instrument rows, and the ERROR path rolls it back so the seed retries."""
    conn.execute(
        "UPDATE idea_seeds SET status = 'CONSUMED', consumed_on = %s,"
        " outcome = %s, theme_slug = %s, detail = %s"
        " WHERE id = %s AND status = 'PENDING'",
        (session, outcome, theme_slug, detail, seed_id),
    )


def list_seeds(conn) -> list[tuple]:
    """PENDING seeds in queue order, then the last 10 terminal rows."""
    return conn.execute(
        "(SELECT id, status, seed_text, created_at, consumed_on, outcome,"
        "  theme_slug FROM idea_seeds WHERE status = 'PENDING'"
        "  ORDER BY created_at, id)"
        " UNION ALL "
        "(SELECT id, status, seed_text, created_at, consumed_on, outcome,"
        "  theme_slug FROM idea_seeds WHERE status <> 'PENDING'"
        "  ORDER BY coalesce(cancelled_at, consumed_on::timestamptz,"
        "  created_at) DESC LIMIT 10)"
    ).fetchall()
