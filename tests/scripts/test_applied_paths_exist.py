"""Every file a script under `scripts/` applies with `kubectl apply -f <path>` exists in the repo.

A manifest moved or retired with its tier leaves a documented step that fails only on a live
cluster. `-f -` (stdin) is not a path. The repository root is written `${LIB_REPO_ROOT}`, `$ROOT`
or `${ROOT}`; any other variable in the path fails here until it is resolved on purpose.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPLY = re.compile(r"kubectl\b[^|;&#]*?\bapply\b[^|;&#]*?(?:-f|--filename)[ =]\"?([^\s\")]+)")
ROOT_VARS = re.compile(r"\$\{LIB_REPO_ROOT\}|\$\{ROOT\}|\$ROOT\b")


def _applied() -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for script in sorted((ROOT / "scripts").rglob("*.sh")):
        for n, line in enumerate(script.read_text().splitlines(), 1):
            rel = str(script.relative_to(ROOT))
            found.extend((rel, n, path) for path in APPLY.findall(line) if path != "-")
    return found


def test_every_applied_path_exists() -> None:
    missing = []
    for script, n, path in _applied():
        resolved = ROOT_VARS.sub(str(ROOT), path)
        if "$" in resolved or not Path(resolved).is_file():
            missing.append(f"{script}:{n}: {path}")
    assert not missing, "kubectl apply -f of a missing or unresolved path:\n" + "\n".join(missing)


def test_the_pattern_sees_a_file_argument() -> None:
    assert APPLY.findall('run kubectl apply -f "${LIB_REPO_ROOT}/a/b.yaml"') == [
        "${LIB_REPO_ROOT}/a/b.yaml"
    ]
    assert APPLY.findall("x | kubectl apply -f -") == ["-"]
