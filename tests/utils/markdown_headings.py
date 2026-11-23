"""Markdown headings and the anchors GitHub gives them, for the doc tests."""

from __future__ import annotations

import re
from pathlib import Path


def slug(heading: str) -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def headings(doc: Path) -> list[tuple[int, str]]:
    """The document's headings (level, text), skipping fenced code."""
    out, fenced = [], False
    for line in doc.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced and (m := re.match(r"(#{1,6}) (.+)$", line)):
            out.append((len(m[1]), m[2]))
    return out


def anchors(doc: Path) -> set[str]:
    """Every heading's anchor; a slug already used earlier in the file gets -1, -2, ..."""
    out: set[str] = set()
    seen: dict[str, int] = {}
    for _, text in headings(doc):
        s = slug(text)
        n = seen.get(s, 0)
        seen[s] = n + 1
        out.add(f"{s}-{n}" if n else s)
    return out
