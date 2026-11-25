"""The project's .venv is built on the pinned Python (the `python` row of tools/pins.tsv) only:
no system interpreter that happens to satisfy requires-python, and no download."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.supply_chain._workflows import REPO

MAKE = shutil.which("make") or "/usr/bin/make"
PINNED = next(
    ln.split("\t")[1]
    for ln in (REPO / "tools/pins.tsv").read_text(encoding="utf-8").splitlines()
    if ln.startswith("python\t")
)


def _env(tmp: Path, *, installed: bool) -> tuple[dict[str, str], Path]:
    """A stub uv that logs its environment and arguments; a fake system python3.14 leads PATH."""
    log = tmp / "uv.log"
    uv = tmp / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        f'echo "$1 $2 UV_PYTHON=$UV_PYTHON PREF=$UV_PYTHON_PREFERENCE DL=$UV_PYTHON_DOWNLOADS'
        f' DIR=$UV_PYTHON_INSTALL_DIR" >> {log}\n'
        f'[ "$1 $2" = "python find" ] && {"exit 0" if installed else "exit 1"}\n'
        "exit 0\n"
    )
    uv.chmod(0o755)
    fake = tmp / "bin"
    fake.mkdir()
    for name in ("python3.14", "python3", "python"):
        (fake / name).write_text("#!/bin/sh\necho fake system python >&2\nexit 9\n")
        (fake / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"}
    for key in [k for k in env if k.startswith("UV_")]:
        del env[key]
    return env, log


def _make(env: dict[str, str], uv: Path, target: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [MAKE, "-C", str(REPO), target, f"UV={uv}"],
        capture_output=True, text=True, env=env, check=False,
    )  # fmt: skip


@pytest.mark.parametrize("target", ["develop", "install"])
def test_sync_gets_the_pinned_python_from_the_repository_never_a_download(
    tmp_path: Path, target: str
) -> None:
    env, log = _env(tmp_path, installed=True)
    r = _make(env, tmp_path / "uv", target)
    assert r.returncode == 0, r.stderr
    sync = next(ln for ln in log.read_text().splitlines() if ln.startswith("sync "))
    assert f"UV_PYTHON={PINNED} " in sync
    assert "PREF=only-managed" in sync
    assert "DL=never" in sync
    assert f"DIR={REPO}/.tools/uv/python" in sync


@pytest.mark.parametrize(
    "target", ["lock", "lock-check", "version-check-ruff", "export-requirements"]
)
def test_uv_calls_that_build_no_venv_get_none_of_the_interpreter_settings(
    tmp_path: Path, target: str
) -> None:
    """Elsewhere they would make uv refuse a .venv built on another interpreter (setup-python)."""
    env, log = _env(tmp_path, installed=True)
    # The stub's empty `uv tree` fails version-check-*; only the log counts.
    _make(env, tmp_path / "uv", target)
    lines = log.read_text().splitlines()
    assert lines
    for ln in lines:
        assert "UV_PYTHON= PREF= DL= DIR=" in ln, ln


def test_a_missing_pinned_python_stops_with_the_way_out_and_syncs_nothing(tmp_path: Path) -> None:
    env, log = _env(tmp_path, installed=False)
    r = _make(env, tmp_path / "uv", "develop")
    assert r.returncode != 0
    assert "run: make tools" in r.stderr
    assert not [ln for ln in log.read_text().splitlines() if ln.startswith("sync ")]


@pytest.mark.skipif(not (REPO / ".tools/bin/uv").exists(), reason="needs the installed uv")
def test_a_system_python_is_never_taken_for_the_venv(tmp_path: Path) -> None:
    """The real uv, an empty Python directory, a system python3.14 on PATH: no venv appears."""
    shutil.copytree(REPO / "tools", tmp_path / "tools")
    shutil.copy(REPO / "Makefile", tmp_path / "Makefile")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\nrequires-python = ">=3.12"\n'
    )
    env, _ = _env(_mk(tmp_path), installed=True)
    env["UV_CACHE_DIR"] = str(tmp_path / "cache")
    r = subprocess.run(
        [MAKE, "-C", str(tmp_path), "develop", f"UV={REPO}/.tools/bin/uv"],
        capture_output=True, text=True, env=env, check=False,
    )  # fmt: skip
    assert r.returncode != 0
    assert "run: make tools" in r.stderr
    assert not (tmp_path / ".venv").exists()


def _mk(tmp: Path) -> Path:
    path = tmp / "stubs"
    path.mkdir()
    return path


def test_ci_setup_python_follows_the_pinned_minor() -> None:
    """CI's venv comes from setup-python and `uv sync --python`, never a download; same minor."""
    action = (REPO / ".github/actions/setup-backend/action.yml").read_text(encoding="utf-8")
    assert re.search(rf'default: "{re.escape(PINNED)}"', action)
