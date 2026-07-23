"""Proposal builder: reviewer-ACTIVATED thesis → sized PROPOSED orders.

Deterministic Python turns research into quantities (Prime Directive: no LLM
number is acted on). Sizing runs here — at proposal time, upstream of the
replay boundary — via the Stage 2 engine's own functions:
identify_clusters → size_basket → enforce_caps → delta vs current position.
"""

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from analytics import volatility
from analytics.engine import clock
from analytics.engine.sizing import enforce_caps, identify_clusters, size_basket
from analytics.engine.state import SHARE_QUANTUM
from config.loader import CONFIG
from data import repository as repo
from schemas.enums import OrderSide, OrderStatus, ThesisStatus
from schemas.models import Document, Order, Thesis

D = Decimal
VOL_LOOKBACK = 120


def _basket_symbols(thesis: Thesis) -> list[str]:
    basket = json.loads(thesis.exposure_basket_approach)
    return [r["symbol"] for r in sorted(basket, key=lambda r: r["rank"])]


def _adj_returns(conn, symbol: str, until: date, n: int) -> list[float]:
    rows = conn.execute(
        "SELECT adj_close FROM price_bars WHERE symbol = %s AND session_date <= %s"
        " ORDER BY session_date DESC LIMIT %s", (symbol, until, n + 1),
    ).fetchall()
    closes = [float(r[0]) for r in reversed(rows)]
    return [b / a - 1 for a, b in zip(closes, closes[1:]) if a > 0]


def _latest_close(conn, symbol: str, until: date) -> Decimal | None:
    row = conn.execute(
        "SELECT close FROM price_bars WHERE symbol = %s AND session_date <= %s"
        " ORDER BY session_date DESC LIMIT 1", (symbol, until),
    ).fetchone()
    return row[0] if row else None


def _current_qty(conn, experiment_id: int, symbol: str) -> Decimal:
    row = conn.execute(
        "SELECT qty FROM positions WHERE experiment_id = %s AND symbol = %s"
        " ORDER BY as_of DESC LIMIT 1", (experiment_id, symbol),
    ).fetchone()
    return row[0] if row else D(0)


def _current_nav(conn, experiment_id: int, initial_cash: Decimal) -> Decimal:
    row = conn.execute(
        "SELECT nav FROM daily_nav WHERE experiment_id = %s ORDER BY nav_date DESC LIMIT 1",
        (experiment_id,),
    ).fetchone()
    return row[0] if row else initial_cash


def cache_packet(conn, packet_path: str, now: datetime) -> int:
    """The decision packet becomes an immutable documents row so orders can

    reference exactly what the human saw (invariant 6 evidence)."""
    import hashlib

    content = open(packet_path, "rb").read()
    h = hashlib.sha256(content).hexdigest()
    existing = repo.document_by_hash(conn, h)
    if existing:
        return existing.id
    doc = Document(
        content_hash=h, raw_bytes=content,
        extracted_text=content.decode("utf-8", errors="replace"),
        retrieval_url=f"file://{packet_path}", source_name="desk_packet",
        ingestion_time=now, first_eligible_decision_time=now,
    )
    repo.insert_document(conn, doc)
    return doc.id


def build_proposals(
    conn, thesis: Thesis, as_of: date, packet_doc_id: int | None = None,
    commit: bool = True,
) -> list[Order]:
    """Sized PROPOSED orders for an ACTIVE thesis. Idempotent: skips if any

    non-final orders already exist for this thesis version."""
    if thesis.status is not ThesisStatus.ACTIVE:
        raise ValueError(f"thesis {thesis.id} is {thesis.status}, not ACTIVE")
    existing = conn.execute(
        "SELECT count(*) FROM orders WHERE thesis_id = %s", (thesis.id,)
    ).fetchone()[0]
    if existing:
        return []

    cfg = CONFIG["engine"]
    symbols = _basket_symbols(thesis)
    prices, vols, returns = {}, {}, {}
    for s in symbols:
        price = _latest_close(conn, s, as_of)
        r = _adj_returns(conn, s, as_of, VOL_LOOKBACK)
        if price is None or len(r) < 30:
            continue  # no price history → not proposable; stays out of the basket
        prices[s] = price
        returns[s] = r
        closes = [D(1)]
        for x in r:
            closes.append(closes[-1] * (1 + D(str(x))))
        vols[s] = D(str(round(volatility.annualized_volatility(closes[-61:]), 6))) or D("0.2")

    if not prices:
        return []

    clusters = identify_clusters(returns, float(cfg["cluster_risk"]["correlation_threshold"]))
    initial_cash = D(cfg["initial_cash"])
    nav = _current_nav(conn, thesis.experiment_id, initial_cash)
    target = size_basket(
        nav=nav,
        cluster_of=clusters,
        vol_by_symbol=vols,
        risk_share_per_cluster=D(cfg["cluster_risk"]["risk_share_per_theme"]),
        prices=prices,
    )
    theme_of = {s: thesis.strategy_tag for s in target}
    target = enforce_caps(
        target, prices=prices, nav=nav,
        theme_of=theme_of, sector_of=theme_of,
        max_single_name_pct=D(cfg["caps"]["max_single_name_pct"]),
        max_theme_pct=D(cfg["caps"]["max_theme_pct"]),
        max_sector_pct=D(cfg["caps"]["max_sector_pct"]),
    )

    now = datetime.now(timezone.utc)
    orders: list[Order] = []
    for symbol in sorted(target):
        delta = (target[symbol] - _current_qty(conn, thesis.experiment_id, symbol)
                 ).quantize(SHARE_QUANTUM)
        if abs(delta) * prices[symbol] < D("50"):  # ignore dust rebalances
            continue
        order = Order(
            experiment_id=thesis.experiment_id,
            thesis_id=thesis.id,
            symbol=symbol,
            side=OrderSide.BUY if delta > 0 else OrderSide.SELL,
            status=OrderStatus.PROPOSED,
            strategy_tag=thesis.strategy_tag,
            proposed_qty=abs(delta),
            decision_packet_doc_id=packet_doc_id,
            proposed_at=now,
            created_at=now,
        )
        repo.insert(conn, order)
        orders.append(order)
    if commit:
        conn.commit()
    return orders
