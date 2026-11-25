"""Every Helm upgrade and rollback the template runs takes ownership of conflicting fields.

Helm 4 applies server-side; a hand edit (kubectl scale, set image, set env, patch) makes its field
manager own the field, and an upgrade or rollback without --force-conflicts then fails on the
conflict, the automatic rollback too. Git is the source of truth: the deploy always wins, and
server-side apply stays on. Static, offline.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# It reads every tracked script and workflow.
pytestmark = pytest.mark.tree_wide

REPO = Path(__file__).resolve().parents[2]
# An invocation, not prose: a command line, a bash array, or a Python argument list.
CALL = re.compile(
    r"^\s*(helm|helm_as)\s[^#]*\b(upgrade|rollback)\b"
    r"|=\(helm (upgrade|rollback)\b|helm=\((upgrade|rollback)\b"
    r'|\[\*[\w.]*helm, "(upgrade|rollback)"'
)


def _calls() -> list[tuple[str, str]]:
    files = subprocess.run(
        ["git", "ls-files", "--", "*.sh", "*.yml", "*.py", "Makefile"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    out = []
    for name in files:
        if name.startswith(("wiki/", "tests/supply_chain/", "tests/scripts/", "tests/backend/")):
            continue
        path = REPO / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8").replace("\\\n", " ")
        out += [(name, ln.strip()) for ln in text.splitlines() if CALL.search(ln)]
    return out


def test_every_upgrade_and_rollback_forces_conflicts() -> None:
    calls = _calls()
    names = {n for n, _ in calls}
    assert {
        "scripts/ci/deploy-env.sh",
        ".github/workflows/deploy-ops.yml",
        "scripts/kind-deploy.sh",
        "scripts/migrate-to-bootstrap.sh",
        "tests/k8s/_kube.py",
        "tests/k8s/test_migrations.py",
    } <= names, names
    for name, line in calls:
        assert "--force-conflicts" in line, (name, line)


def test_the_local_apply_forces_conflicts_too() -> None:
    text = (REPO / "scripts" / "k8s-env.sh").read_text(encoding="utf-8")
    line = next(ln for ln in text.splitlines() if "local -a helm=(upgrade" in ln)
    assert "--force-conflicts" in line


def test_server_side_apply_is_never_turned_off() -> None:
    for name, line in _calls():
        assert "--server-side=false" not in line, (name, line)
        assert "--server-side false" not in line, (name, line)
