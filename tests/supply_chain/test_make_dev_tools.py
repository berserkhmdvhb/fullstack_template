"""`make dev-tools-outdated` (part of check-all-dry) is read-only and survives a missing network."""

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
UV = str(ROOT / ".tools/bin/uv") if (ROOT / ".tools/bin/uv").exists() else shutil.which("uv")
pytestmark = pytest.mark.skipif(UV is None or shutil.which("make") is None, reason="needs uv, make")


def _make(target: str, tmp: Path, *, offline: bool) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "UV_CACHE_DIR": str(tmp / "cache"), "UV_HTTP_RETRIES": "0"}
    if offline:
        env["HTTPS_PROXY"] = "http://127.0.0.1:1"  # a closed port: every index call fails
    return subprocess.run(
        ["make", "-s", f"UV={UV}", target],  # noqa: S607
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip


def test_the_outdated_check_exits_zero_and_says_so_when_the_index_is_unreachable(
    tmp_path: Path,
) -> None:
    result = _make("dev-tools-outdated", tmp_path, offline=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "could not check" in result.stdout
    assert "is not in uv.lock" not in result.stdout


def test_an_unknown_package_is_still_reported_offline(tmp_path: Path) -> None:
    result = _make("version-check-no-such-package", tmp_path, offline=True)
    assert result.returncode != 0
    assert "is not in uv.lock" in result.stdout


def test_the_dev_group_holds_no_runtime_dependency() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = {
        d.split("[")[0].split(">")[0].split("=")[0].strip().lower()
        for d in pyproject["project"]["dependencies"]
    }
    dev = {
        d.split("[")[0].split(">")[0].split("=")[0].strip().lower()
        for d in pyproject["dependency-groups"]["dev"]
    }
    assert not runtime & dev
