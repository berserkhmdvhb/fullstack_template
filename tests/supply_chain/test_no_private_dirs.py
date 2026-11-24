"""No tracked file may name the two non-public working directories (agent plans, review packages),
or cite the non-public design and plan they hold.

They are untracked and absent from every clone, so a tracked file that names one leaks their
existence and does nothing, and a citation of a section of them ("design section 11.4") points
nowhere. The names are built from parts, so this file does not hold them.

The guards are the template's own: an adopted repository may keep such directories, or use such
phrases, for its own reasons (tests/utils/template_tree.py tells the two apart).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.utils.template_tree import is_template

pytestmark = pytest.mark.tree_wide

ROOT = Path(__file__).resolve().parent.parent.parent
PLANS = "wiki" + "/" + "plans"
PRIVATE = (PLANS, "." + "super" + "powers")
CITATION = re.compile(
    r"design section|design §|design doc|spec §|spec section|plan task|plan ledger|review focus"
    r"|the design allows",
    re.IGNORECASE,
)
TEMPLATE_ONLY = pytest.mark.skipif(
    not is_template(ROOT), reason="an adopted copy: the non-public directories are the template's"
)
# The files that hold the phrasings as patterns, not as citations.
CITATION_PATTERNS = {
    "tests/supply_chain/test_no_private_dirs.py",
    "tests/supply_chain/test_renovate_config.py",
}


def tracked_paths() -> list[str]:
    run = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607
        cwd=ROOT, capture_output=True, check=True,
    )  # fmt: skip
    return [os.fsdecode(raw) for raw in filter(None, run.stdout.split(b"\0"))]


def private_paths(paths: list[str]) -> list[str]:
    return [rel for rel in paths if any(rel.startswith(f"{name}/") for name in PRIVATE)]


@TEMPLATE_ONLY
def test_no_tracked_path_is_inside_a_private_directory() -> None:
    bad = private_paths(tracked_paths())
    assert not bad, f"a non-public directory is tracked: {bad}"


def test_the_path_matcher_flags_both_prefixes() -> None:
    for name in PRIVATE:
        assert private_paths([f"{name}/x.md", "wiki/ci-cd.md"]) == [f"{name}/x.md"]


def tracked_text() -> list[tuple[str, str]]:
    found = []
    for rel in tracked_paths():
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            found.append((rel, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue  # a binary file
    return found


def private_lines(rel: str, text: str) -> list[str]:
    if rel.startswith(f"{PLANS}/"):
        return []
    return [
        f"{rel}:{n}"
        for n, line in enumerate(text.splitlines(), 1)
        if any(name in line for name in PRIVATE)
    ]


@TEMPLATE_ONLY
def test_no_tracked_file_names_a_private_directory() -> None:
    bad = [hit for rel, text in tracked_text() for hit in private_lines(rel, text)]
    assert not bad, f"a non-public directory is named at: {bad}"


def test_the_matcher_flags_both_names() -> None:
    for name in PRIVATE:
        assert private_lines("lychee.toml", f'ok\n  "^{name}/",\n') == ["lychee.toml:2"]
    assert private_lines("README.md", "wiki/ and plans\n") == []


def citation_lines(rel: str, text: str) -> list[str]:
    if rel.startswith(f"{PLANS}/") or rel in CITATION_PATTERNS:
        return []
    return [f"{rel}:{n}" for n, line in enumerate(text.splitlines(), 1) if CITATION.search(line)]


@TEMPLATE_ONLY
def test_no_tracked_file_cites_the_non_public_design_or_plan() -> None:
    bad = [hit for rel, text in tracked_text() for hit in citation_lines(rel, text)]
    assert not bad, f"a non-public design or plan is cited at: {bad}"


def test_the_citation_matcher_flags_the_phrasings_and_not_public_sections() -> None:
    for line in (
        "# Design section 11.4: x",
        "see the spec §5",
        "(see Review Focus)",
        "plan Task 3",
    ):
        assert citation_lines("a.sh", f"ok\n{line}\n") == ["a.sh:2"], line
    assert citation_lines("a.md", "[GKE §12.3](./gke-cluster-setup.md), the OpenAPI spec\n") == []
