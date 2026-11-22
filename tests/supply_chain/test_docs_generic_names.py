"""The template carries generic names only: no tracked file may use the owner's project names."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
# SHA-256 of the lower-case form of the owner's own project names, kept out of the template.
# Digests only, so this public file does not carry the words.
BANNED = {
    "7dd042d2737683ee617f8f786bf8571c8ebc0bd28f2102bbbe57ff2635b99636",
    "210c02b1472d4b8d7d2cf4b57f2e6cf646cc11916ae6ba8c576dbb5143c9b949",
    "74822c43b337f00f89fe6451e101df1c55ac1ca0e4eace284527698687708465",
}
PLAN_ID = re.compile(r"\(L[0-9]+\.[0-9x]+\)")
SELF = "tests/supply_chain/test_docs_generic_names.py"


def tracked_text() -> list[tuple[str, str]]:
    run = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607
        cwd=ROOT, capture_output=True, check=True,
    )  # fmt: skip
    found = []
    for raw in filter(None, run.stdout.split(b"\0")):
        rel = os.fsdecode(raw)
        path = ROOT / rel
        if rel == SELF or not path.is_file():
            continue
        try:
            found.append((rel, path.read_text(encoding="utf-8")))
        except UnicodeDecodeError:
            continue  # a binary file
    return found


def banned_lines(text: str, banned: set[str]) -> list[int]:
    """Line numbers holding a word whose digest is in `banned`."""
    return [
        n
        for n, line in enumerate(text.splitlines(), 1)
        for word in re.findall(r"[a-z0-9]+", line.lower())
        if hashlib.sha256(word.encode()).hexdigest() in banned
    ]


def test_no_owner_project_names_in_tracked_files() -> None:
    bad = [f"{rel}:{n}" for rel, text in tracked_text() for n in banned_lines(text, BANNED)]
    assert not bad, f"owner project name found at: {bad}"


def test_no_foreign_plan_ids_in_wiki() -> None:
    bad = [
        rel
        for rel, text in tracked_text()
        if rel.startswith("wiki/") and rel.endswith(".md") and PLAN_ID.search(text)
    ]
    assert not bad, f"foreign plan item id in: {bad}"


def test_matcher_flags_a_synthetic_word() -> None:
    synthetic = {hashlib.sha256(b"examplebanned").hexdigest()}
    assert banned_lines("ok\nsome ExampleBanned here\n", synthetic) == [2]
    assert banned_lines("nothing here\n", synthetic) == []
