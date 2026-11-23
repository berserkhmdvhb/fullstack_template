"""Markdown as GitHub reads it, for the doc tests: the lines outside fenced code, the headings,
the anchors GitHub gives them and the link targets of a line. Enough CommonMark for the wiki."""

from __future__ import annotations

import re
from pathlib import Path

# A fence opens with 3+ backticks or tildes, at any indent (a list item's content is indented), and
# closes with the same character, at least as many, and nothing else on the line.
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_LINK_TEXT = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def _scan(doc: Path) -> tuple[list[tuple[int, str]], int | None]:
    """The lines outside fenced code, and the line of a fence left open at the end (or None)."""
    out: list[tuple[int, str]] = []
    fence, opened = "", 0
    for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        m = _FENCE.match(line)
        if fence:
            if m and m[1][0] == fence[0] and len(m[1]) >= len(fence) and not line.strip(" `~"):
                fence = ""
        elif m:
            fence, opened = m[1], n
        else:
            out.append((n, line))
    return out, (opened if fence else None)


def prose_lines(doc: Path) -> list[tuple[int, str]]:
    """The (line number, text) of every line outside fenced code, without the fences' own lines."""
    return _scan(doc)[0]


def unclosed_fence(doc: Path) -> int | None:
    """The line of a fence that never closes (it would hide the rest of the document), or None."""
    return _scan(doc)[1]


def slug(heading: str) -> str:
    """GitHub's heading anchor: the heading's text (a link counts as its text, code as its content),
    lower case, punctuation dropped, spaces to hyphens."""
    text = _LINK_TEXT.sub(r"\1", re.sub(r"\s+#+\s*$", "", heading))
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


def headings(doc: Path) -> list[tuple[int, str]]:
    """The document's headings (level, text), outside fenced code."""
    return [(len(m[1]), m[2]) for _, line in prose_lines(doc) if (m := _HEADING.match(line))]


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


def _destination(text: str, i: int) -> str | None:
    """The link destination starting at text[i] (just after "]("): <...>, or up to the first space
    or unbalanced ")"; an optional title after it is skipped."""
    if text.startswith("<", i):
        end = text.find(">", i)
        return text[i + 1 : end] if end != -1 else None
    depth, j = 0, i
    while j < len(text) and not text[j].isspace():
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            if depth == 0:
                break
            depth -= 1
        j += 1
    return text[i:j] or None


def links(line: str) -> list[str]:
    """The destinations of the inline links and images on a line, outside code spans."""
    text = _CODE_SPAN.sub("", line)
    found = (_destination(text, m.end()) for m in re.finditer(r"\]\(", text))
    return [d for d in found if d is not None]
