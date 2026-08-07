"""HTML companion renderer — pure text-in/HTML-out; publish() writes into
a tmp dir via config override. Network-free."""

from reporting import deepdive_html as dh

SAMPLE = """# Deep dive 2026-W32: test-theme: AAA, BBB

> [!warning] Degraded rendering
> - something dropped

## Executive summary

Body text with a citation [^A1] and **bold**.

## Exposure scoreboard

| Rank | Symbol | Exposure |
|---|---|---|
| 1 | AAA | 1 |

```mermaid
xychart-beta
    title "Measured exposure score by name"
    x-axis [AAA, BBB]
    y-axis 0 --> 1
    bar [1, 0.4]
```

> [!note]- Raw causal chain (analyst JUDGMENT)
> collapsible body

## Provenance appendix

[^A1]: SOURCE_FACT — doc 9: "verbatim span"
"""


def test_parse_xychart_grammar():
    chart = dh.parse_xychart(
        'xychart-beta\n    title "T"\n    x-axis [a, b]\n'
        "    y-axis 0 --> 1\n    bar [1, 0.4]\n    line [0.2, 0.3]\n")
    assert chart["title"] == "T"
    assert chart["labels"] == ["a", "b"]
    assert chart["y_range"] == (0.0, 1.0)
    assert chart["series"] == [("bar", [1.0, 0.4]), ("line", [0.2, 0.3])]
    assert dh.parse_xychart("flowchart TD\nA-->B") is None  # not our grammar


def test_render_page_end_to_end():
    page = dh.render_page(SAMPLE, title="Deep dive test")
    assert page.startswith("<!doctype html>")
    assert "<title>Deep dive test</title>" in page
    # mermaid became inline SVG, no fence or placeholder left behind
    assert '<svg class="xychart"' in page
    assert "mermaid" not in page and "XYCHARTBLOCK" not in page
    assert "Measured exposure score by name" in page
    # callouts got typed classes, marker text stripped
    assert 'blockquote class="warning"' in page
    assert 'blockquote class="note"' in page
    assert "[!warning]" not in page and "[!note]" not in page
    # tables and footnotes resolved
    assert "<table>" in page
    assert 'class="footnote-ref"' in page and "verbatim span" in page
    # section nav from the ## headings
    assert 'nav class="sections"' in page and "Executive summary" in page
    # self-contained: no external fetches of any kind
    assert "http" not in page.split("</title>")[1].split("<body>")[0]
    assert "<script" not in page and "src=" not in page


def test_unparseable_mermaid_degrades_to_pre():
    page = dh.render_page("```mermaid\nflowchart TD\nA-->B\n```",
                          title="x")
    assert "<pre>" in page and "flowchart TD" in page


def test_publish_writes_atomic_and_returns_url(tmp_path, monkeypatch):
    monkeypatch.setitem(
        dh.CONFIG, "reporting",
        {"deepdive_html": {"enabled": True, "out_dir": str(tmp_path),
                           "url_base": "https://host.ts.net/desk-reports/"}})
    url = dh.publish(SAMPLE, "2026-W32-test-theme", title="t")
    assert url == "https://host.ts.net/desk-reports/2026-W32-test-theme.html"
    written = tmp_path / "2026-W32-test-theme.html"
    assert written.exists() and "<svg" in written.read_text()
    assert not list(tmp_path.glob(".*.tmp"))     # no temp litter
    # idempotent overwrite
    assert dh.publish(SAMPLE, "2026-W32-test-theme", title="t") == url


def test_publish_disabled_is_none(tmp_path, monkeypatch):
    monkeypatch.setitem(dh.CONFIG, "reporting",
                        {"deepdive_html": {"enabled": False}})
    assert dh.publish(SAMPLE, "x", title="t") is None


def test_publish_without_url_base_writes_but_no_push_url(tmp_path, monkeypatch):
    monkeypatch.setitem(
        dh.CONFIG, "reporting",
        {"deepdive_html": {"enabled": True, "out_dir": str(tmp_path),
                           "url_base": ""}})
    assert dh.publish(SAMPLE, "y", title="t") is None
    assert (tmp_path / "y.html").exists()        # file still lands
