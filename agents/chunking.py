"""Deterministic chunk + retrieve for filing-length documents.

No embeddings for the slice: sections are scored by term frequency against the
configured emphasis terms, and the top-K sections (by score, ties broken by
document order) are concatenated under the context budget. Deterministic:
same text + same terms → same context.
"""

import re


def strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[a-zA-Z#0-9]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_sections(text: str, target_chars: int = 6000) -> list[str]:
    """Split on paragraph-ish boundaries into ~target_chars sections."""
    words = text.split(" ")
    sections, current, size = [], [], 0
    for w in words:
        current.append(w)
        size += len(w) + 1
        if size >= target_chars:
            sections.append(" ".join(current))
            current, size = [], 0
    if current:
        sections.append(" ".join(current))
    return sections


def score_section(section: str, terms: list[str]) -> int:
    lower = section.lower()
    return sum(lower.count(t.lower()) for t in terms)


def relevant_excerpt(text: str, terms: list[str], budget_chars: int = 24000) -> str:
    """Top-scoring sections concatenated in document order, within budget."""
    sections = split_sections(text)
    scored = sorted(
        ((score_section(s, terms), i) for i, s in enumerate(sections)),
        key=lambda t: (-t[0], t[1]),
    )
    chosen: list[int] = []
    used = 0
    for score, i in scored:
        if score == 0:
            break
        if used + len(sections[i]) > budget_chars:
            continue
        chosen.append(i)
        used += len(sections[i])
    return "\n\n".join(sections[i] for i in sorted(chosen))
