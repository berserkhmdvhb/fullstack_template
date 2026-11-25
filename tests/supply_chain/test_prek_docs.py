"""No doc tells a reader to install or run pre-commit; prek is the git-hook runner."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.tree_wide
ROOT = Path(__file__).resolve().parent.parent.parent
# Where the word may stay: the released history, and the ADR that weighs the old runner.
ALLOWED = {"wiki/changelog.md", "wiki/adr/0044-prek-for-git-hooks.md"}
# The assessment names the old runner as what prek replaced; it may not tell a reader to use it.
HISTORY = {"wiki/tooling-assessment.md"}
WORD = re.compile(r"pre-commit(?!-config)", re.IGNORECASE)
COMMAND = re.compile(
    r"pre-commit (install|run|autoupdate)|bin/pre-commit|install pre-commit|pre-commit\.txt"
)


def tracked() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"],  # noqa: S607
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return [p for p in out.split() if p.endswith((".md", ".mdx", ".json")) and p not in ALLOWED]


def hits(pattern: re.Pattern[str], skip: set[str]) -> list[str]:
    found = []
    for rel in tracked():
        if rel in skip or not (ROOT / rel).is_file():
            continue
        for n, line in enumerate((ROOT / rel).read_text().splitlines(), 1):
            if pattern.search(line):
                found.append(f"{rel}:{n}")
    return found


def test_no_doc_names_pre_commit_but_the_history_and_the_assessment() -> None:
    assert not hits(WORD, HISTORY)


def test_no_doc_tells_a_reader_to_install_or_run_pre_commit() -> None:
    assert not hits(COMMAND, set())
