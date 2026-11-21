"""Every lockfile package with a dependency install script is reviewed in `allowScripts`.

npm 11.20 reads `allowScripts` (`name` or `name@version` to true or false). The policy is
`npm ci --ignore-scripts`, so every entry is a denial unless it is also
documented as an exception in wiki/frontend-deployment.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parent.parent.parent
DOC = (ROOT / "wiki/frontend-deployment.md").read_text(encoding="utf-8")


def _name(path: str) -> str:
    return path.rsplit("node_modules/", 1)[-1]


def _entry_name(key: str) -> str:
    return re.sub(r"(?<=.)@[^@]*$", "", key)


def unreviewed(lock: dict[str, Any], allow: dict[str, bool]) -> list[str]:
    reviewed = {_entry_name(k) for k in allow}
    return sorted(
        {_name(p) for p, v in lock["packages"].items() if v.get("hasInstallScript")} - reviewed
    )


def _lock() -> dict[str, Any]:
    return cast(
        "dict[str, Any]",
        json.loads((ROOT / "frontend/package-lock.json").read_text(encoding="utf-8")),
    )


def _allow() -> dict[str, bool]:
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    return cast("dict[str, bool]", package.get("allowScripts", {}))


def test_the_guard_flags_a_new_install_script() -> None:
    lock = {
        "packages": {
            "": {},
            "node_modules/old": {"hasInstallScript": True},
            "node_modules/a/node_modules/fresh": {"hasInstallScript": True},
            "node_modules/plain": {},
        }
    }
    assert unreviewed(lock, {"old@1.0.0": False}) == ["fresh"]
    assert unreviewed(lock, {"old": False, "fresh": False}) == []


def test_every_lockfile_install_script_is_reviewed() -> None:
    assert unreviewed(_lock(), _allow()) == [], (
        "add each to package.json allowScripts and document it"
    )


def test_an_approved_script_is_a_documented_exception() -> None:
    approved = [_entry_name(k) for k, v in _allow().items() if v is True]
    for name in approved:
        assert f"`{name}`" in DOC, (
            f"{name} runs its install script: document why in frontend-deployment.md"
        )
