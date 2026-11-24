# your-service-name — adaptable service template (see wiki/getting-started.md)
"""`make test` runs the unit, integration and e2e suites, not the live or the contract one."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from _pytest.mark.expression import Expression

ROOT = Path(__file__).resolve().parent.parent


def selects(command: str, marker: str | None) -> bool:
    """Whether a pytest command line selects a test with this marker (None: an unmarked test)."""
    args = shlex.split(command.split(" -m pytest ", 1)[-1])
    assert "addopts=" in args, "the default marker filter (unit only) must be cleared"
    expression = args[args.index("-m") + 1] if "-m" in args else ""
    if not expression:
        return True

    def has(name: str, /, **_: str | int | bool | None) -> bool:
        return name == marker

    return Expression.compile(expression).evaluate(has)


def test_make_test_runs_unit_integration_and_e2e_only() -> None:
    out = subprocess.run(
        ["make", "-s", "-n", "test"],  # noqa: S607 - make from PATH
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    (command,) = [ln for ln in out.splitlines() if re.search(r"-m pytest\b", ln)]
    assert " tests/ " in f"{command} "
    for marker in (None, "integration", "e2e"):
        assert selects(command, marker), marker
    for marker in ("live", "contract"):
        assert not selects(command, marker), marker


def test_the_selection_check_sees_a_unit_only_command() -> None:
    unit_only = 'pytest tests/ -o addopts="" -m "not integration and not e2e and not live"'
    assert selects(unit_only, None)
    assert not selects(unit_only, "integration")
