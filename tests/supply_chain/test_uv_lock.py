"""The backend is locked with uv: uv.lock is hashed, records the 7-day rule, and is checked."""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
LOCK = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))


def test_the_7_day_rule_is_a_relative_span_the_lock_records() -> None:
    # A span is stored as `exclude-newer-span`, not as a clock reading, so `uv lock --check` gives
    # the same answer on any later day.
    assert PYPROJECT["tool"]["uv"]["exclude-newer"] == "7 days"
    assert LOCK["options"]["exclude-newer-span"] == "P7D"


def test_dev_dependencies_are_a_pep_735_group_not_an_extra() -> None:
    assert "optional-dependencies" not in PYPROJECT["project"]
    assert "ruff" in " ".join(PYPROJECT["dependency-groups"]["dev"])


def test_license_is_a_pep_639_expression() -> None:
    project = PYPROJECT["project"]
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert "version" in project["dynamic"]
    assert PYPROJECT["build-system"]["requires"][0] == "setuptools>=80"


def test_every_registry_package_in_the_lock_carries_hashes() -> None:
    registry = [p for p in LOCK["package"] if "registry" in p["source"]]
    assert registry
    for pkg in registry:
        files = [*pkg.get("wheels", []), *([pkg["sdist"]] if "sdist" in pkg else [])]
        assert files, pkg["name"]
        for item in files:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", item["hash"]), pkg["name"]


def test_the_lock_names_no_local_path_but_the_project_itself() -> None:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert "file://" not in text
    assert not re.search(r"/(home|Users|tmp)/", text)
    local = [p["name"] for p in LOCK["package"] if "registry" not in p["source"]]
    assert local == [PYPROJECT["project"]["name"].replace("_", "-")]


def test_a_pre_commit_hook_checks_the_lock_with_the_pinned_uv() -> None:
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook = next(h for r in config["repos"] for h in r["hooks"] if h["id"] == "uv-lock")
    assert "uv lock --check" in hook["entry"]
    assert re.search(hook["files"], "pyproject.toml")
    assert re.search(hook["files"], "uv.lock")
    assert not re.search(hook["files"], "src/pyproject.toml")


def test_make_targets_use_the_locked_set() -> None:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "$(UV) sync --locked --no-dev" in text
    assert "$(UV) sync --locked\n" in text
    assert "$(UV) lock --check" in text
    assert "pip install" not in text
