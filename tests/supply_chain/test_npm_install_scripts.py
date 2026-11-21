"""Every lockfile package with a dependency install script is reviewed in `allowScripts`.

npm 11.20 reads `allowScripts` in `frontend/package.json`. Policy here:
- a denial (`false`) is keyed by the bare package name, so a version bump stays denied;
- an approval (`true`) is `name@<exact locked version>` with its own row in the Exceptions section
  of wiki/frontend-deployment.md, so a bump of an approved package forces a re-review;
- a key npm would drop is a failure here. npm's `validatePolicy` (npm 11.20,
  lib/utils/resolve-allow-scripts.js) ignores ranges, dist-tags and unparseable entries with a
  warning, which silently leaves the package unreviewed. `name@*`, `||` lists and empty names are
  rejected too: only a bare name or one exact version is accepted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent.parent
DOC = (ROOT / "wiki/frontend-deployment.md").read_text(encoding="utf-8")

NAME = r"(?:@[a-z0-9~._-]+/)?[a-z0-9~._-]+"
EXACT = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
BARE = re.compile(NAME)
PINNED = re.compile(rf"({NAME})@({EXACT})")


def _name(path: str) -> str:
    return path.rsplit("node_modules/", 1)[-1]


def exceptions_section(doc: str) -> str:
    """The text under the `Exceptions` heading, up to the next heading."""
    match = re.search(r"^#{2,6} Exceptions\n(.*?)(?=^#{1,6} |\Z)", doc, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def problems(lock: dict[str, Any], allow: dict[str, bool], doc: str) -> list[str]:
    found: list[str] = []
    denied: set[str] = set()
    approved: set[tuple[str, str]] = set()
    section = exceptions_section(doc)
    for key, value in allow.items():
        if value is False and BARE.fullmatch(key):
            denied.add(key)
        elif value is True and (pin := PINNED.fullmatch(key)):
            approved.add((pin[1], pin[2]))
            if f"`{key}`" not in section:
                found.append(f"{key}: approved without a row in the Exceptions section")
        else:
            found.append(f"{key}: a denial needs a bare name, an approval name@exact-version")
    scripts = {
        (_name(p), v["version"]) for p, v in lock["packages"].items() if v.get("hasInstallScript")
    }
    for name, version in sorted(scripts):
        if name not in denied and (name, version) not in approved:
            found.append(f"{name}@{version}: has an install script and no allowScripts entry")
    for name, version in sorted(approved - scripts):
        found.append(f"{name}@{version}: approved, but the lockfile has no such install script")
    return found


def _lock() -> dict[str, Any]:
    return cast(
        "dict[str, Any]",
        json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8")),
    )


def _allow() -> dict[str, bool]:
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    return cast("dict[str, bool]", package.get("allowScripts", {}))


LOCK = {
    "packages": {
        "": {},
        "node_modules/old": {"version": "1.2.3", "hasInstallScript": True},
        "node_modules/a/node_modules/fresh": {"version": "2.0.0", "hasInstallScript": True},
        "node_modules/plain": {"version": "1.0.0"},
    }
}
ROW = "#### Exceptions\n\n| `old@1.2.3` | why |\n\n## Next\n"


def test_the_guard_flags_a_new_install_script() -> None:
    assert problems(LOCK, {"old": False}, "") == [
        "fresh@2.0.0: has an install script and no allowScripts entry"
    ]
    assert problems(LOCK, {"old": False, "fresh": False}, "") == []


def test_an_unpinned_approval_fails() -> None:
    assert problems(LOCK, {"old": True, "fresh": False}, ROW)


def test_a_stale_pinned_approval_fails() -> None:
    out = problems(LOCK, {"old@1.2.2": True, "fresh": False}, "#### Exceptions\n`old@1.2.2`\n")
    assert any("no such install script" in p for p in out)
    assert any("old@1.2.3" in p for p in out)


def test_an_approval_without_an_exceptions_row_fails() -> None:
    out = problems(LOCK, {"old@1.2.3": True, "fresh": False}, "#### Exceptions\n\nNone.\n")
    assert out == ["old@1.2.3: approved without a row in the Exceptions section"]


def test_an_approval_documented_only_outside_the_section_fails() -> None:
    doc = "see `old@1.2.3` here\n\n#### Exceptions\n\nNone.\n\n## Other\n`old@1.2.3`\n"
    assert problems(LOCK, {"old@1.2.3": True, "fresh": False}, doc)


def test_a_pinned_documented_approval_passes() -> None:
    assert problems(LOCK, {"old@1.2.3": True, "fresh": False}, ROW) == []


def test_keys_npm_ignores_fail() -> None:
    for key in (
        "old@^1.2.0",
        "old@~1.2.0",
        "old@>=1",
        "old@latest",
        "old@*",
        "old@1.2.3||1.2.4",
        "",
        "@",
        "old@",
    ):
        assert problems(LOCK, {key: False, "old": False, "fresh": False}, ROW), key
        assert problems(LOCK, {key: True, "old": False, "fresh": False}, ROW), key


def test_the_lockfile_install_scripts_are_reviewed() -> None:
    assert problems(_lock(), _allow(), DOC) == []
