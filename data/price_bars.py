"""price_bars read-through cache (migration 002).

Derived from immutable Tiingo documents, never authoritative. Deterministic
rebuild rule: per symbol, the latest-ingested tiingo document wins wholesale
(no per-row merging); parsing is the SAME `parse_bars` the adapter uses, so
there is no second parser to drift. `TRUNCATE price_bars` + `rebuild` must be
bit-identical — proven by tests/test_price_bars.py.

    uv run python -m data.price_bars    # rebuild from cached documents
"""

from data import repository as repo
from data.adapters.tiingo import parse_bars
from data.db import connect
from schemas.models import Document, PriceBar


def _latest_docs_by_symbol(conn) -> dict[str, Document]:
    docs = repo.fetch(conn, Document, "source_name = 'tiingo'")
    latest: dict[str, Document] = {}
    for doc in sorted(docs, key=lambda d: (d.ingestion_time, d.id)):
        url = doc.retrieval_url
        # .../tiingo/daily/{symbol}/prices
        symbol = url.rstrip("/").split("/")[-2].upper()
        latest[symbol] = doc  # later ingestion wins wholesale
    return latest


def rebuild_price_bars(conn, winners: dict[str, Document] | None = None) -> int:
    """Idempotent full rebuild: delete + reinsert from the winning documents.

    `winners` overrides the default latest-ingested-wins choice per symbol —
    used by Stage 3's restatement guard to keep a prior document pinned while
    a REVISION incident awaits the human. Unlisted symbols use the default."""
    conn.execute("DELETE FROM price_bars")
    n = 0
    latest = _latest_docs_by_symbol(conn)
    if winners:
        latest = {**latest, **winners}
    for symbol in sorted(latest):
        doc = latest[symbol]
        for bar in parse_bars(doc, symbol):
            repo.insert(
                conn,
                PriceBar(
                    symbol=bar.symbol,
                    session_date=bar.session,
                    open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                    volume=bar.volume,
                    adj_open=bar.adj_open, adj_high=bar.adj_high,
                    adj_low=bar.adj_low, adj_close=bar.adj_close,
                    adj_volume=bar.adj_volume,
                    split_factor=bar.split_factor, div_cash=bar.div_cash,
                    source_doc_hash=doc.content_hash,
                ),
            )
            n += 1
    return n  # caller commits (or is inside a transaction context)


def table_fingerprint(conn) -> str:
    """Canonical hash of the table contents (Decimals as text, fixed order)."""
    import hashlib

    h = hashlib.sha256()
    rows = conn.execute(
        "SELECT symbol, session_date, open::text, high::text, low::text, close::text,"
        " volume, adj_open::text, adj_high::text, adj_low::text, adj_close::text,"
        " adj_volume::text, split_factor::text, div_cash::text, source_doc_hash"
        " FROM price_bars ORDER BY symbol, session_date"
    ).fetchall()
    for row in rows:
        h.update("\x1f".join(str(v) for v in row).encode())
        h.update(b"\x1e")
    return h.hexdigest()


if __name__ == "__main__":
    with connect() as conn:
        n = rebuild_price_bars(conn)
        conn.commit()
    print(f"price_bars rebuilt: {n} rows")
