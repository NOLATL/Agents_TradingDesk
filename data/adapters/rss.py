"""RSS adapter for the research candidate scout (absorbed from
Agents_Economics 2026-07-22). Raw feed responses are cached as immutable
documents (invariant 4); headline extraction is a deterministic parse of the
cached bytes — stdlib XML, no feedparser dependency. A malformed feed yields
zero headlines, never an exception.

Headlines feed the candidate scout's narrative context only — they never
become assertions and never enter basket construction (invariant 5 is
enforced downstream at the evidence boundary).

    uv run python -m data.adapters.rss
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

import yaml

from data.adapters.core import fetch_and_cache
from data.db import connect
from data import repository as repo
from schemas.models import Document

DISCOVERY_PATH = Path(__file__).parent.parent.parent / "config" / "discovery.yaml"
LICENSE_NOTE = "public RSS/Atom feed; titles/links used for internal research only"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
SUMMARY_CHARS = 400  # per-item cap; Substack feeds carry full article bodies


def discovery_config() -> dict:
    return yaml.safe_load(DISCOVERY_PATH.read_text())


def feed_urls() -> list[str]:
    return [
        url for group in discovery_config().get("rss_feeds", {}).values()
        for url in group
    ]


def ingest_feed(conn, url: str) -> tuple[Document, bool]:
    return fetch_and_cache(
        conn,
        url=url,
        params=None,
        source_name="rss",
        license_note=LICENSE_NOTE,
        # Research-side narrative context: eligible immediately at ingestion —
        # headlines are never decision inputs, so next-session gating would
        # only starve the nightly scout of the day's news.
        first_eligible_decision_time=datetime.now(timezone.utc),
    )


def ingest_all() -> dict[str, bool]:
    results = {}
    with connect() as conn:
        for url in feed_urls():
            try:
                _, fetched = ingest_feed(conn, url)
                results[url] = fetched
            except Exception:
                results[url] = False  # feed down tonight; scout runs on the rest
        conn.commit()
    return results


def _clean_summary(raw: str | None) -> str | None:
    """HTML → plain text, whitespace-collapsed, word-boundary truncated.
    Stdlib only (no feedparser, per module contract)."""
    if not raw:
        return None
    text = " ".join(unescape(re.sub(r"<[^>]+>", " ", raw)).split())
    if not text:
        return None
    if len(text) > SUMMARY_CHARS:
        text = text[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
    return text


def parse_items(doc: Document) -> list[dict]:
    """Deterministic parse of a cached feed document →
    [{title, summary, published}]. Summary comes from RSS <description> /
    <content:encoded> or Atom <summary>/<content>; None when absent."""
    try:
        root = ET.fromstring(doc.raw_bytes.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return []
    out = []
    # RSS 2.0
    for item in root.iter("item"):
        title = item.findtext("title")
        if not title:
            continue
        published = None
        pub = item.findtext("pubDate")
        if pub:
            try:
                published = parsedate_to_datetime(pub)
            except (TypeError, ValueError):
                published = None
        summary = _clean_summary(
            item.findtext("description") or item.findtext(f"{CONTENT_NS}encoded")
        )
        out.append({"title": " ".join(title.split()),
                    "summary": summary, "published": published})
    # Atom
    for entry in root.iter(f"{ATOM_NS}entry"):
        title = entry.findtext(f"{ATOM_NS}title")
        if not title:
            continue
        published = None
        stamp = entry.findtext(f"{ATOM_NS}published") or entry.findtext(f"{ATOM_NS}updated")
        if stamp:
            try:
                published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            except ValueError:
                published = None
        summary = _clean_summary(
            entry.findtext(f"{ATOM_NS}summary") or entry.findtext(f"{ATOM_NS}content")
        )
        out.append({"title": " ".join(title.split()),
                    "summary": summary, "published": published})
    return out


def parse_headlines(doc: Document) -> list[dict]:
    """Deterministic parse of a cached feed document → [{title, published}]."""
    return [
        {"title": i["title"], "published": i["published"]}
        for i in parse_items(doc)
    ]


def recent_items(conn, hours: int = 168, limit: int = 120) -> list[dict]:
    """Deduped {title, summary} items from the newest cached copy of each
    feed, newest first. Undated items are kept (many feeds omit dates) but
    sorted last."""
    docs = repo.fetch(
        conn, Document,
        "source_name = 'rss' ORDER BY ingestion_time DESC",
    )
    newest_per_feed: dict[str, Document] = {}
    for doc in docs:
        newest_per_feed.setdefault(doc.retrieval_url, doc)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = []
    for doc in newest_per_feed.values():
        for item in parse_items(doc):
            published = item["published"]
            if published is not None and published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published is not None and published < cutoff:
                continue
            rows.append((published or datetime.min.replace(tzinfo=timezone.utc), item))
    rows.sort(key=lambda r: r[0], reverse=True)
    seen, items = set(), []
    for _, item in rows:
        if item["title"] not in seen:
            seen.add(item["title"])
            items.append({"title": item["title"], "summary": item["summary"]})
    return items[:limit]


def recent_headlines(conn, hours: int = 168, limit: int = 120) -> list[str]:
    """Deduped titles only (theme briefs); see recent_items for summaries."""
    return [i["title"] for i in recent_items(conn, hours=hours, limit=limit)]


if __name__ == "__main__":
    for url, fetched in ingest_all().items():
        print(f"{url}: {'fetched' if fetched else 'cache hit / failed'}")
