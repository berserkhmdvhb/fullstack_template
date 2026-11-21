"""One exclude list: the adapter and the leftover check both read infra/adapt/excludes.txt."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADAPT = ROOT / "infra" / "adapt"
LISTED = [
    ln.strip()
    for ln in (ADAPT / "excludes.txt").read_text(encoding="utf-8").splitlines()
    if ln.strip() and not ln.startswith("#")
]
READERS = [
    ADAPT / "replace_service_name.py",
    ADAPT / "adopt_stamp.py",
    ADAPT / "replace_service_name.sh",
    ADAPT / "adopt.sh",
    ROOT / "scripts" / "ci" / "check-placeholder-leftovers.sh",
]


def _common() -> object:
    sys.path.insert(0, str(ADAPT))
    spec = importlib.util.spec_from_file_location("adapt_common", ADAPT / "_common.py")
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_adapter_default_is_the_file_list() -> None:
    assert _common().load_excludes() == set(LISTED)  # type: ignore[attr-defined]


def test_no_script_keeps_a_second_hand_written_list() -> None:
    for path in READERS:
        text = path.read_text(encoding="utf-8")
        assert ".mypy_cache" not in text, f"{path.name} hard-codes an exclude list"
        assert "wiki," not in text, f"{path.name} hard-codes an exclude list"


def test_the_leftover_check_reads_the_same_file() -> None:
    text = (ROOT / "scripts" / "ci" / "check-placeholder-leftovers.sh").read_text(encoding="utf-8")
    assert "infra/adapt/excludes.txt" in text


def test_adapter_help_names_the_shared_file() -> None:
    out = subprocess.run(
        [sys.executable, str(ADAPT / "replace_service_name.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "excludes.txt" in out


def test_tool_installs_and_build_output_are_excluded() -> None:
    required = {".tools", "node_modules", "frontend/node_modules", "frontend/.next", "output"}
    required |= {".venv", ".adapt-backup"}
    assert required <= set(LISTED)


def test_the_adapter_rewrites_uv_lock_so_the_locked_project_name_follows_the_rename() -> None:
    exts = _common().DEFAULT_INCLUDE_EXTS.split(",")  # type: ignore[attr-defined]
    assert ".lock" in exts


def test_a_rename_keeps_pyproject_and_uv_lock_consistent(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src" / "your_service_name").mkdir(parents=True)
    (root / "src" / "your_service_name" / "__init__.py").write_text("")
    for name in ("pyproject.toml", "uv.lock"):
        (root / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run(
        [  # noqa: S607
            "bash", str(ADAPT / "replace_service_name.sh"),
            "-n", "my_svc", "-o", "your_service_name", "-r", str(root),
        ],
        cwd=root, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    normalised = re.sub(r"[-_.]+", "-", pyproject["project"]["name"]).lower()
    assert normalised == "my-svc"
    local = [p["name"] for p in lock["package"] if "registry" not in p["source"]]
    assert local == [normalised]
    for name in ("pyproject.toml", "uv.lock"):
        text = (root / name).read_text(encoding="utf-8")
        assert "your-service-name" not in text
        assert "your_service_name" not in text
