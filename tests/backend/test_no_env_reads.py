# your-service-name — adaptable service template (see wiki/getting-started.md)
"""``Settings`` is the only reader of the environment: nothing else in ``src/`` may touch it."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED = {Path("your_service_name/backend/settings.py")}
ENV_ATTRS = {"getenv", "getenvb", "environ", "environb", "putenv", "unsetenv"}


def find_env_reads(src: Path) -> list[str]:
    """Return ``path:line what`` for every direct environment access outside ``ALLOWED``."""
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if path.relative_to(src) in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in ENV_ATTRS
                and isinstance(node.value, ast.Name)
                and node.value.id == "os"
            ):
                found.append(f"{path.relative_to(src)}:{node.lineno} os.{node.attr}")
            elif isinstance(node, ast.ImportFrom) and node.module == "os":
                found += [
                    f"{path.relative_to(src)}:{node.lineno} from os import {a.name}"
                    for a in node.names
                    if a.name in ENV_ATTRS
                ]
    return found


def test_src_has_no_environment_reads_outside_settings() -> None:
    assert list(SRC.rglob("settings.py")), "src scan found no settings.py; the path is wrong"
    assert find_env_reads(SRC) == []


def test_the_scanner_catches_each_form(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        'import os\nos.getenv("X")\nos.environ["Y"]\nfrom os import getenv\n', encoding="utf-8"
    )
    assert len(find_env_reads(tmp_path)) == 3  # noqa: PLR2004 — three forms planted above
