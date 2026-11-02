"""AI-bubble feeds: parsers on minimal fixtures, the Vast display-only rule,
and the quarantine boundary. Network-free."""

import json
import re
import zipfile
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from data.adapters import bubble_feeds as feeds
from schemas.models import Document

ROOT = Path(__file__).parent.parent


def _doc(raw: bytes) -> Document:
    now = __import__("datetime").datetime(2026, 11, 2, tzinfo=__import__("datetime").timezone.utc)
    return Document(content_hash="h", raw_bytes=raw, extracted_text=None, retrieval_url="x",
                    query_params={}, source_name="azure_retail", license_note="t",
                    ingestion_time=now, event_time=None, publication_time=None,
                    first_eligible_decision_time=now)


def _xlsx(*sheets: str) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, body in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml",
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       f"<sheetData>{body}</sheetData></worksheet>")
    return buf.getvalue()


def _inline(ref: str, text: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def test_parse_azure_per_gpu_and_skips_windows():
    items = [
        {"retailPrice": 18.0, "type": "Consumption", "skuName": "ND96isrH100v5 Spot",
         "productName": "Virtual Machines NDsr H100 v5 Series"},
        {"retailPrice": 99.0, "type": "Consumption", "skuName": "ND96isrH100v5 Spot",
         "productName": "Virtual Machines NDsr H100 v5 Series Windows"},
        {"retailPrice": 87600.0, "type": "Reservation", "reservationTerm": "1 Year",
         "skuName": "ND96isrH100v5", "productName": "Virtual Machines NDsr H100 v5 Series"},
    ]
    out = feeds.parse_azure(_doc(json.dumps({"Items": items}).encode()))
    assert out == {"spot": Decimal("2.25"), "reserved_1y": Decimal("1.25")}


def test_parse_finra_dates_month_after_and_handles_december():
    raw = _xlsx(f'<row r="1">{_inline("A1", "Year-Month")}</row>'
                f'<row r="2">{_inline("A2", "2026-09")}<c r="B2"><v>100</v></c></row>'
                f'<row r="3">{_inline("A3", "2025-12")}<c r="B3"><v>90</v></c></row>')
    assert feeds.parse_finra(raw) == [(date(2026, 1, 1), Decimal(90)), (date(2026, 10, 1), Decimal(100))]


def test_parse_btos_matches_question_and_skips_suppressed():
    header = _inline("F1", "202621") + _inline("G1", "202620") + _inline("H1", "202619")
    yes = (_inline("A2", "XX") + _inline("B2", "7") + _inline("E2", "Yes")
           + _inline("F2", "32.1%") + _inline("G2", "33.7%") + _inline("H2", "S"))
    noise = _inline("A3", "XX") + _inline("B3", "24") + _inline("E3", "Yes") + _inline("F3", "99%")
    dates = ('<row r="1"/>'
             f'<row r="2">{_inline("D2", "202620")}<c r="E2"><v>46000</v></c><c r="F2"><v>46013</v></c></row>'
             f'<row r="3">{_inline("D3", "202621")}<c r="E3"><v>46014</v></c><c r="F3"><v>46027</v></c></row>')
    raw = _xlsx(f'<row r="1">{header}</row><row r="2">{yes}</row><row r="3">{noise}</row>',
                "", "", "", dates)
    out = feeds.parse_btos(raw)
    assert [v for _, v in out] == [Decimal("33.7"), Decimal("32.1")]
    assert out[0][0] < out[1][0]


def test_vast_never_reaches_scoring_or_documents():
    """Contract: Vast's license bars index/benchmark use. Display only."""
    cfg_text = (ROOT / "config/experiment.yaml").read_text()
    signals = cfg_text[cfg_text.index("  signals:\n", cfg_text.index("bubble_monitor:")):]
    assert "vast" not in signals.lower().split("\nreporting:")[0]
    src = (ROOT / "data/adapters/bubble_feeds.py").read_text()
    body = src[src.index("def refresh_vast"):src.index("def read_vast")]
    assert "fetch_and_cache" not in body


def test_monitor_never_imported_by_llm_or_engine_layers():
    """Invariant 5: nothing the analyst, scout or engine runs may see this."""
    pattern = re.compile(r"bubble_monitor|bubble_feeds")
    for folder in ("agents", "research", "analytics/engine"):
        for path in (ROOT / folder).rglob("*.py"):
            assert not pattern.search(path.read_text()), path
