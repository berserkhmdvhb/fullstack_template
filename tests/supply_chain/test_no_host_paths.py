"""No tracked text file may carry a path that belongs to one machine (home or scratch dir)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.tree_wide

ROOT = Path(__file__).resolve().parent.parent.parent
# Generic on purpose: nothing here is derived from the machine the test runs on.
HOST_PATHS = [
    re.compile(r"/tmp/claude"),  # noqa: S108
    re.compile(r"/home/(?!appuser\b)[A-Za-z0-9._-]+/"),
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
]
LOCK_ABSOLUTE_VIA = re.compile(r"#.*\s-[rc]\s+/")
# This file spells the patterns out as examples.
SELF = "tests/supply_chain/test_no_host_paths.py"


def tracked_text() -> list[tuple[str, str]]:
    run = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607
        cwd=ROOT, capture_output=True, check=True,
    )  # fmt: skip
    names = run.stdout.split(b"\0")
    found = []
    for raw in filter(None, names):
        rel = raw.decode()
        path = ROOT / rel
        if rel == SELF or not path.is_file():
            continue
        try:
            found.append((rel, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue  # a binary file
    return found


def hits(rel: str, text: str) -> list[str]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if any(p.search(line) for p in HOST_PATHS):
            found.append(f"{rel}:{number}: a host-specific path")
        if rel.startswith("tools/requirements/") and LOCK_ABSOLUTE_VIA.search(line):
            found.append(f"{rel}:{number}: an absolute `# via -r/-c` path")
    return found


def test_no_tracked_file_has_a_host_specific_path() -> None:
    problems = [hit for rel, text in tracked_text() for hit in hits(rel, text)]
    assert not problems, "\n".join(problems)


def test_the_guard_catches_what_it_should_and_spares_container_paths() -> None:
    bad = [
        "cache at /tmp/claude-1000/x",
        "path /home/alice/project",
        "path /Users/bob/project",
    ]
    for line in bad:
        assert hits("README.md", line), line
    assert hits("tools/requirements/x.txt", "    # via -r /scratch/in.txt")
    assert hits("tools/requirements/x.txt", "    #   -c /tmp/abc/constraints.txt")
    for line in ("USER /home/appuser/app", "dir /tmp/nginx_cache", "out /tmp/r2-check.txt"):
        assert not hits("Dockerfile", line), line
    assert not hits("tools/requirements/x.txt", "    # via yamale")
