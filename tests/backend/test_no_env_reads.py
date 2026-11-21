# your-service-name — adaptable service template (see wiki/getting-started.md)
"""``Settings`` is the only reader of the environment: nothing else in ``src/`` may touch it."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWED = {Path("your_service_name/backend/settings.py")}
ENV_ATTRS = {"getenv", "getenvb", "environ", "environb", "putenv", "unsetenv"}


def _os_access(node: ast.AST, os_names: set[str]) -> list[str]:
    """``os.getenv`` / ``os.environ`` (through any alias) and ``from os import getenv/*``."""
    if (
        isinstance(node, ast.Attribute)
        and node.attr in ENV_ATTRS
        and isinstance(node.value, ast.Name)
        and node.value.id in os_names
    ):
        return [f"os.{node.attr}"]
    if isinstance(node, ast.ImportFrom) and node.module == "os":
        return [f"from os import {a.name}" for a in node.names if a.name in ENV_ATTRS | {"*"}]
    return []


def _dotenv_import(node: ast.AST) -> list[str]:
    """Any import of ``dotenv``: a second environment source the guard cannot see."""
    if isinstance(node, ast.Import):
        return [f"import {a.name}" for a in node.names if a.name.split(".")[0] == "dotenv"]
    if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "dotenv":
        return [f"from {node.module} import ..."]
    return []


def _settings_class(node: ast.AST) -> list[str]:
    """A ``BaseSettings`` subclass: a second Settings class outside ``settings.py``."""
    if not isinstance(node, ast.ClassDef):
        return []
    names = [b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in node.bases]
    return [f"class {node.name}(BaseSettings)"] if "BaseSettings" in names else []


def _scan(tree: ast.AST) -> list[tuple[int, str]]:
    """Every direct environment access in one module: ``(line, what)``."""
    os_names = {
        a.asname or a.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for a in node.names
        if a.name == "os"
    }
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        what = _os_access(node, os_names) + _dotenv_import(node) + _settings_class(node)
        found += [(getattr(node, "lineno", 0), w) for w in what]
    return found


def find_env_reads(src: Path) -> list[str]:
    """Return ``path:line what`` for every environment access outside ``ALLOWED``."""
    found: list[str] = []
    for path in sorted(src.rglob("*.py")):
        rel = path.relative_to(src)
        if rel in ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found += [f"{rel}:{line} {what}" for line, what in _scan(tree)]
    return found


def test_src_has_no_environment_reads_outside_settings() -> None:
    assert list(SRC.rglob("settings.py")), "src scan found no settings.py; the path is wrong"
    assert find_env_reads(SRC) == []


@pytest.mark.parametrize(
    "source",
    [
        'import os\nos.getenv("X")',
        'import os\nos.environ["Y"]',
        'import os\nos.environ.get("Y")',
        'import os as o\no.getenv("X")',
        'import sys, os as operating\noperating.environ["Y"]',
        "from os import getenv",
        "from os import environ as e",
        "from os import *",
        "import dotenv",
        "import dotenv.main",
        "from dotenv import load_dotenv",
        "from pydantic_settings import BaseSettings\nclass S(BaseSettings): ...",
        "import pydantic_settings\nclass S(pydantic_settings.BaseSettings): ...",
    ],
)
def test_the_scanner_catches_each_form(tmp_path: Path, source: str) -> None:
    (tmp_path / "a.py").write_text(source + "\n", encoding="utf-8")
    assert find_env_reads(tmp_path), source


def test_the_scanner_ignores_unrelated_code(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        'import os\nos.chdir("x")\nvalue = {}.get("getenv")\n', encoding="utf-8"
    )
    assert find_env_reads(tmp_path) == []
