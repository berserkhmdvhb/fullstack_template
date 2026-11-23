# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The doc-test helper reads Markdown as GitHub does, for the cases the wiki uses."""

from __future__ import annotations

from pathlib import Path

from tests.utils.markdown_headings import anchors, links, prose_lines, slug


def _doc(tmp_path: Path, text: str) -> Path:
    doc = tmp_path / "doc.md"
    doc.write_text(text, encoding="utf-8")
    return doc


def test_an_indented_fence_hides_its_contents(tmp_path: Path) -> None:
    doc = _doc(
        tmp_path, "1. Step\n\n   ```bash\n   # not a heading\n   [a](gone.md)\n   ```\n\n## After\n"
    )
    assert [text for _, text in prose_lines(doc)] == ["1. Step", "", "", "## After"]
    assert anchors(doc) == {"after"}


def test_tilde_and_longer_fences_close_only_on_their_own_kind(tmp_path: Path) -> None:
    doc = _doc(tmp_path, "~~~\n# a\n```\n# b\n~~~\n````md\n```\n# c\n````\n# out\n")
    assert anchors(doc) == {"out"}


def test_a_link_with_a_title_and_a_url_with_parentheses() -> None:
    assert links('[a](x.md "t") and [b](y.md#z) and [c](<w x.md>)') == ["x.md", "y.md#z", "w x.md"]
    assert links("[w](https://example.com/a_(b)) then [d](d.md)") == [
        "https://example.com/a_(b)",
        "d.md",
    ]


def test_links_in_inline_code_are_not_links() -> None:
    assert links("`[a](x.md)` and ``[b](y.md)`` but [c](z.md)") == ["z.md"]


def test_a_heading_with_a_link_slugs_to_its_text(tmp_path: Path) -> None:
    assert anchors(_doc(tmp_path, "## [Velero](https://velero.io) backups\n")) == {"velero-backups"}


def test_a_heading_with_inline_code_and_a_closing_sequence() -> None:
    assert slug("GET `/api/v1/health`") == "get-apiv1health"
    assert slug("Expand/contract policy ##") == "expandcontract-policy"


def test_a_repeated_heading_gets_a_counter(tmp_path: Path) -> None:
    assert anchors(_doc(tmp_path, "# A\n## A\n### A\n")) == {"a", "a-1", "a-2"}
