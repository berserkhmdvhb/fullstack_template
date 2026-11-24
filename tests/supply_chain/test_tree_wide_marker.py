"""A test module that enumerates the whole tracked tree carries `tree_wide`, so the repo job runs it
on every pull request instead of leaving it behind the backend path filter."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.supply_chain._workflows import REPO

TESTS = REPO / "tests"
# `git ls-files` with no pathspec (a closing `]` or `)` straight after it, or only `-z` / flags).
WALKS_THE_TREE = re.compile(r'"ls-files"(?:,\s*"-[-a-z]+")*\s*\]')
MARKED = re.compile(r"^pytestmark = pytest\.mark\.tree_wide$", re.MULTILINE)


def unmarked(tests: Path) -> list[str]:
    return [
        str(p.relative_to(tests))
        for p in sorted(tests.rglob("test_*.py"))
        if p != Path(__file__)
        and WALKS_THE_TREE.search(p.read_text("utf-8"))
        and not MARKED.search(p.read_text("utf-8"))
    ]


def test_every_module_that_walks_the_tracked_tree_is_marked_tree_wide() -> None:
    assert unmarked(TESTS) == []


def test_an_unmarked_walker_is_caught(tmp_path: Path) -> None:
    (tmp_path / "test_walker.py").write_text('run(["git", "ls-files", "-z"])\n', encoding="utf-8")
    assert unmarked(tmp_path) == ["test_walker.py"]


@pytest.mark.parametrize("pathspec", ['"*.md"', '"--", *SCOPE'])
def test_a_scoped_listing_is_not_a_tree_walk(tmp_path: Path, pathspec: str) -> None:
    (tmp_path / "test_scoped.py").write_text(
        f'run(["git", "ls-files", {pathspec}])\n', encoding="utf-8"
    )
    assert unmarked(tmp_path) == []
