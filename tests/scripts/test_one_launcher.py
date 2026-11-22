# your-service-name — adaptable service template (see wiki/getting-started.md)
"""One launcher: every way of starting the backend goes through the serve module."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVE = "your_service_name.backend.serve"
RE_SERVE = re.escape(SERVE)
# Each way of starting the backend, and the text that runs the serve module.
LAUNCH_PATHS = {
    "run_backend.sh": rf"^exec python -m {RE_SERVE}$",
    # Inside start_backend(), and not in a comment.
    "infra/runtime/entrypoint.sh": rf"start_backend\(\) \{{[^}}]*^\s*python -m {RE_SERVE} &$",
    ".github/workflows/reusable-backend.yml": rf"^[^#\n]*python -m {RE_SERVE}\b",
    "Dockerfile.backend": rf'^CMD \[.*"-m", "{RE_SERVE}"',
    # The container command, a list item.
    "charts/your-service-name/templates/backend-deployment.yaml": rf"^\s+- {RE_SERVE}$",
}
# A command that starts Uvicorn directly: `uvicorn app`, `exec uvicorn`, `python -m uvicorn`.
BARE_UVICORN = re.compile(
    r"^[^#\n]*(?:\buvicorn[ \t]+[\"$\w.-]|-m[ \t]+uvicorn\b|need_cmd[ \t]+uvicorn)", re.MULTILINE
)


def _tracked() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git on PATH
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    suffixes = {".sh", ".bash", ".yml", ".yaml", ".tpl", ".bats", ".json", ".py", ".toml"}
    names = {"Makefile", "run_backend.sh"}
    return [
        ROOT / name
        for name in out.split("\0")
        if name
        and not name.startswith(("wiki/", "tests/", "node_modules/", "frontend/"))
        and (Path(name).suffix in suffixes or Path(name).name in names or "Dockerfile" in name)
    ]


@pytest.mark.parametrize(("path", "pattern"), LAUNCH_PATHS.items())
def test_every_launch_path_runs_the_serve_module(path: str, pattern: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    assert re.search(pattern, text, re.MULTILINE), path


def test_no_launch_path_runs_bare_uvicorn() -> None:
    found = [
        str(p.relative_to(ROOT))
        for p in _tracked()
        if BARE_UVICORN.search(p.read_text(encoding="utf-8"))
    ]
    assert found == []


@pytest.mark.parametrize("path", ["run_backend.sh", "infra/runtime/entrypoint.sh"])
def test_the_dev_launchers_reload_only_through_the_serve_module(path: str) -> None:
    """Hot reload is the serve module's BACKEND_RELOAD option, never a bare `uvicorn --reload`."""
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "--reload" not in text
    assert "BACKEND_RELOAD" in text


def test_the_dev_runner_reloads_by_default_and_the_entrypoint_does_not() -> None:
    assert 'BACKEND_RELOAD="${BACKEND_RELOAD:-true}"' in (ROOT / "run_backend.sh").read_text()
    entry = (ROOT / "infra/runtime/entrypoint.sh").read_text(encoding="utf-8")
    assert 'BACKEND_RELOAD="${BACKEND_RELOAD:-false}"' in entry
