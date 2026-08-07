"""Deep dive → self-contained HTML companion (2026-08-07, Jared's ask:
StockRover-style readability with real charts, delivered like the daily
briefing's read-along page).

The vault markdown stays the artifact of record (Obsidian: wikilinks,
footnote provenance, mermaid). This module renders THE SAME rendered
markdown to one standalone HTML file — no CDN, no JS, charts become
inline SVG parsed from the report's own mermaid xychart blocks (a bounded
grammar we emit ourselves in reporting/deepdive.py::_mermaid_xychart).
Pure text-in/HTML-out except `publish`, which writes the file into the
tailscale-served output dir and returns the URL for the ntfy push.

Best-effort by contract: called from deepdive.write() inside try/except —
an HTML failure never fails the dive or the weekly cycle.
"""

import html as html_lib
import re
from pathlib import Path

import markdown as md_lib

from config.loader import CONFIG

OUT_DIR = Path(__file__).parent / "output" / "deepdive-html"

_CSS = """
:root { color-scheme: light dark; }
body {
  font: 16px/1.55 system-ui, -apple-system, sans-serif;
  margin: 0 auto; max-width: 52rem; padding: 1.25rem 1.1rem 4rem;
}
h1 { font-size: 1.45rem; line-height: 1.3; }
h2 {
  font-size: 1.2rem; margin-top: 2.4rem;
  border-bottom: 2px solid #4682b4; padding-bottom: .25rem;
}
h3 { font-size: 1.02rem; margin-top: 1.5rem; }
a { color: #0b62c4; }
@media (prefers-color-scheme: dark) { a { color: #8ab4f8; } }
nav.sections {
  font-size: .88rem; margin: 1rem 0 0; padding: .6rem .8rem;
  border: 1px solid rgba(128,128,128,.35); border-radius: .5rem;
}
p.meta { font-size: .85rem; opacity: .75; margin: .4rem 0 0; }
table { border-collapse: collapse; display: block; overflow-x: auto;
        font-size: .92rem; margin: .8rem 0; }
th { background: rgba(70,130,180,.18); position: sticky; top: 0; }
th, td {
  border: 1px solid rgba(128,128,128,.4);
  padding: .3rem .55rem; text-align: left; white-space: nowrap;
}
tbody tr:nth-child(even) { background: rgba(128,128,128,.08); }
blockquote {
  margin: 1rem 0; padding: .6rem .9rem; border-left: 4px solid #999;
  background: rgba(128,128,128,.08); border-radius: 0 .4rem .4rem 0;
}
blockquote.warning { border-left-color: #d9822b; background: rgba(217,130,43,.1); }
blockquote.note { border-left-color: #4682b4; background: rgba(70,130,180,.1); }
blockquote p:first-child { margin-top: 0; }
blockquote p:last-child { margin-bottom: 0; }
svg.xychart { max-width: 100%; height: auto; display: block; margin: 1rem 0; }
svg.xychart .bar { fill: #4682b4; opacity: .85; }
svg.xychart .line { stroke: #d9822b; stroke-width: 2; fill: none; }
svg.xychart .dot { fill: #d9822b; }
svg.xychart text { fill: currentColor; font: 11px system-ui, sans-serif; }
svg.xychart .title { font-size: 13px; font-weight: 600; }
svg.xychart .grid { stroke: rgba(128,128,128,.35); stroke-width: 1; }
.footnote { font-size: .82em; opacity: .85; overflow-wrap: break-word; }
.footnote li p { white-space: normal; }
hr { border: none; border-top: 1px solid rgba(128,128,128,.35); margin: 2rem 0; }
li { margin: .15rem 0; }
code { font-size: .88em; background: rgba(128,128,128,.15);
       padding: .05rem .3rem; border-radius: .25rem; }
"""

_MERMAID_RE = re.compile(r"```mermaid\n(.*?)```", re.S)
_CALLOUT_RE = re.compile(
    r"<blockquote>\s*<p>\[!(\w+)\]-?\s*", re.S)


# --------------------------------------------------------------------------- #
# mermaid xychart → inline SVG (parses only the grammar deepdive.py emits)
# --------------------------------------------------------------------------- #

def _floats(bracketed: str) -> list[float]:
    inner = bracketed.strip()[1:-1]
    return [float(x) for x in inner.split(",")] if inner.strip() else []


def _labels(bracketed: str) -> list[str]:
    inner = bracketed.strip()[1:-1]
    return [x.strip() for x in inner.split(",")] if inner.strip() else []


def parse_xychart(block: str) -> dict | None:
    """Parse our own xychart-beta emission; None for anything else (the
    block then stays a plain <pre> — degrade, never guess)."""
    if "xychart-beta" not in block:
        return None
    out = {"title": "", "labels": [], "series": [], "y_range": None}
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith("title"):
            m = re.search(r'"(.*)"', line)
            out["title"] = m.group(1) if m else ""
        elif line.startswith("x-axis"):
            out["labels"] = _labels(line[len("x-axis"):])
        elif line.startswith("y-axis"):
            m = re.match(r"y-axis\s+(-?[\d.]+)\s*-->\s*(-?[\d.]+)", line)
            if m:
                out["y_range"] = (float(m.group(1)), float(m.group(2)))
        elif line.startswith(("bar ", "bar[")):
            out["series"].append(("bar", _floats(line[3:])))
        elif line.startswith(("line ", "line[")):
            out["series"].append(("line", _floats(line[4:])))
    return out if out["labels"] and out["series"] else None


def xychart_svg(chart: dict) -> str:
    """One bounded, dependency-free SVG: bars + overlaid lines, four y
    gridlines, rotated x labels when crowded."""
    width, height = 720, 300
    ml, mr, mt, mb = 56, 12, 34, 56
    pw, ph = width - ml - mr, height - mt - mb
    labels = chart["labels"]
    all_vals = [v for _, vs in chart["series"] for v in vs]
    lo, hi = ((chart["y_range"]) if chart["y_range"]
              else (min(all_vals + [0]), max(all_vals)))
    if hi == lo:
        hi = lo + 1
    n = max(len(labels), *(len(vs) for _, vs in chart["series"]))
    step = pw / max(n, 1)

    def x(i: float) -> float:
        return ml + step * (i + 0.5)

    def y(v: float) -> float:
        return mt + ph * (1 - (v - lo) / (hi - lo))

    parts = [f'<svg class="xychart" viewBox="0 0 {width} {height}"'
             f' role="img" aria-label="{html_lib.escape(chart["title"])}">']
    if chart["title"]:
        parts.append(f'<text class="title" x="{width / 2:.0f}" y="18"'
                     f' text-anchor="middle">{html_lib.escape(chart["title"])}</text>')
    for k in range(5):
        gv = lo + (hi - lo) * k / 4
        gy = y(gv)
        parts.append(f'<line class="grid" x1="{ml}" y1="{gy:.1f}"'
                     f' x2="{width - mr}" y2="{gy:.1f}"/>')
        parts.append(f'<text x="{ml - 6}" y="{gy + 4:.1f}"'
                     f' text-anchor="end">{gv:g}</text>')
    rotate = len(labels) > 8
    for i, lab in enumerate(labels):
        lx, ly = x(i), height - mb + 16
        attrs = (f' transform="rotate(-40 {lx:.1f} {ly})" text-anchor="end"'
                 if rotate else ' text-anchor="middle"')
        parts.append(f'<text x="{lx:.1f}" y="{ly}"{attrs}>'
                     f'{html_lib.escape(lab)}</text>')
    bw = step * 0.55
    for kind, vals in chart["series"]:
        if kind == "bar":
            for i, v in enumerate(vals):
                top, base = min(y(v), y(min(max(lo, 0), hi))), max(y(v), y(min(max(lo, 0), hi)))
                parts.append(f'<rect class="bar" x="{x(i) - bw / 2:.1f}"'
                             f' y="{top:.1f}" width="{bw:.1f}"'
                             f' height="{max(base - top, 1):.1f}"/>')
        else:
            pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
            parts.append(f'<polyline class="line" points="{pts}"/>')
            for i, v in enumerate(vals):
                parts.append(f'<circle class="dot" cx="{x(i):.1f}"'
                             f' cy="{y(v):.1f}" r="3"/>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# markdown → page
# --------------------------------------------------------------------------- #

def _swap_mermaid(md_text: str) -> tuple[str, dict[str, str]]:
    """Replace mermaid fences with placeholders; return SVG (or escaped
    <pre> fallback) per placeholder."""
    stash: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        key = f"XYCHARTBLOCK{len(stash)}XY"
        chart = parse_xychart(m.group(1))
        stash[key] = (xychart_svg(chart) if chart
                      else f"<pre>{html_lib.escape(m.group(1))}</pre>")
        return f"\n{key}\n"

    return _MERMAID_RE.sub(repl, md_text), stash


def render_page(md_text: str, *, title: str) -> str:
    """Full standalone HTML document from a rendered deep-dive markdown."""
    body, stash = _swap_mermaid(md_text)
    converter = md_lib.Markdown(
        extensions=["tables", "footnotes", "toc", "sane_lists"])
    content = converter.convert(body)
    content = _CALLOUT_RE.sub(
        lambda m: f'<blockquote class="{html_lib.escape(m.group(1).lower())}"><p>',
        content)
    for key, svg in stash.items():
        content = content.replace(f"<p>{key}</p>", svg).replace(key, svg)

    toc = [(t["id"], t["name"]) for t in converter.toc_tokens
           for t in ([t] + t.get("children", [])) if t["level"] == 2]
    nav = ""
    if toc:
        links = " &middot; ".join(
            f'<a href="#{cid}">{html_lib.escape(name)}</a>' for cid, name in toc)
        nav = f'<nav class="sections"><strong>Sections:</strong> {links}</nav>\n'
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html_lib.escape(title)}</title>\n"
        f"<style>{_CSS}</style>\n"
        "</head>\n<body>\n"
        f"{nav}"
        f"{content}\n"
        "</body>\n</html>\n"
    )


def publish(md_text: str, name_stem: str, *, title: str) -> str | None:
    """Render + write into the served dir; return the tailnet URL (None
    when the feature is off or no url_base is configured)."""
    cfg = CONFIG.get("reporting", {}).get("deepdive_html", {})
    if not cfg.get("enabled"):
        return None
    out_dir = Path(cfg.get("out_dir") or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / f"{name_stem}.html"
    tmp = out_dir / f".{name_stem}.html.tmp"
    tmp.write_text(render_page(md_text, title=title), encoding="utf-8")
    tmp.replace(final)  # atomic; idempotent overwrite (re-runs re-render)
    base = str(cfg.get("url_base") or "").rstrip("/")
    return f"{base}/{final.name}" if base else None
