# your-service-name — adaptable service template (see wiki/getting-started.md)
"""One launcher: every way of starting the backend goes through the serve module."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVE = "your_service_name.backend.serve"
# Each way of starting the backend, and the text that runs the serve module.
LAUNCH_PATHS = {
    "run_backend.sh": f"exec python -m {SERVE}",
    "infra/runtime/entrypoint.sh": f"python -m {SERVE} &",
    ".github/workflows/reusable-backend.yml": f"python -m {SERVE}",
    "Dockerfile.backend": f'"-m", "{SERVE}"',
    "charts/your-service-name/templates/backend-deployment.yaml": SERVE,
}
# A command that starts Uvicorn directly: `uvicorn app`, `exec uvicorn`, `python -m uvicorn`.
BARE_UVICORN = re.compile(
    r"^[^#\n]*(?:\buvicorn\s+[\"$\w.-]|-m\s+uvicorn\b|need_cmd\s+uvicorn)", re.MULTILINE
)


def _tracked() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git on PATH
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    suffixes = {".sh", ".bash", ".yml", ".yaml", ".tpl", ".bats"}
    names = {"Makefile", "run_backend.sh"}
    return [
        ROOT / name
        for name in out.split("\0")
        if name
        and not name.startswith(("wiki/", "tests/", "node_modules/", "frontend/", ".superpowers/"))
        and (Path(name).suffix in suffixes or Path(name).name in names or "Dockerfile" in name)
    ]


@pytest.mark.parametrize(("path", "needle"), LAUNCH_PATHS.items())
def test_every_launch_path_runs_the_serve_module(path: str, needle: str) -> None:
    assert needle in (ROOT / path).read_text(encoding="utf-8")


def test_no_launch_path_runs_bare_uvicorn() -> None:
    found = [
        str(p.relative_to(ROOT))
        for p in _tracked()
        if BARE_UVICORN.search(p.read_text(encoding="utf-8"))
    ]
    assert found == []


@pytest.mark.parametrize("path", ["run_backend.sh", "infra/runtime/entrypoint.sh"])
def test_the_dev_launchers_do_not_reload(path: str) -> None:
    """Reload runs the app in a child process: the metrics server would serve an empty registry."""
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "--reload" not in text
    assert "BACKEND_RELOAD" not in text
