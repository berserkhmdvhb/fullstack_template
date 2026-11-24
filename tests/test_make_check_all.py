# your-service-name — adaptable service template (see wiki/getting-started.md)
"""`make check-all` is `make check-all-dry`, each read-only check swapped for its rewriting twin."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The read-only check of check-all-dry -> the target check-all runs in its place.
TWINS = {
    "ruff-format-check": "ruff-format",
    "ruff-lint": "ruff-lint-fix",
    "env-docs-check": "env-docs",
}


def prerequisites(target: str) -> list[str]:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    line = re.search(rf"^{re.escape(target)}:([^\n=]*)$", text, re.MULTILINE)
    assert line, f"no {target} rule"
    return line[1].split()


def test_check_all_runs_what_check_all_dry_runs_but_rewrites() -> None:
    dry = prerequisites("check-all-dry")
    assert "env-docs-check" in dry
    assert prerequisites("check-all") == [TWINS.get(t, t) for t in dry]
