# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Every relative link in the wiki resolves: its file exists and its #anchor names a heading there
(GitHub's anchor, with -1, -2, ... for a repeated heading). Links inside code are not links."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.utils.markdown_headings import anchors, links, prose_lines

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "wiki").glob("*.md"))


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_relative_link_resolves(doc: Path) -> None:
    broken = []
    for n, url in [(n, u) for n, line in prose_lines(doc) for u in links(line)]:
        if re.match(r"[a-z][a-z0-9+.-]*:", url):  # https:, mailto: ...
            continue
        path, _, anchor = url.partition("#")
        target = (doc.parent / path).resolve() if path else doc
        if not target.exists():
            broken.append(f"{doc.name}:{n}: {url} (no such file)")
        elif anchor and target.suffix == ".md" and anchor not in anchors(target):
            broken.append(f"{doc.name}:{n}: {url} (no such heading)")
    assert not broken, "\n".join(broken)
