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


def test_a_git_hook_checks_the_lock_with_the_pinned_uv() -> None:
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


GETTING_STARTED = (ROOT / "wiki/getting-started.md").read_text(encoding="utf-8")


def test_the_build_backend_is_pinned_with_hashes_in_the_lock() -> None:
    constraints = {c["name"]: c for c in LOCK["manifest"]["build-constraints"]}
    assert {"setuptools", "setuptools-scm"} <= set(constraints)
    for item in constraints.values():
        assert item["specifier"].startswith("==")
        assert item["hashes"]
    assert PYPROJECT["tool"]["uv"]["build-constraint-dependencies"]


def test_the_security_update_procedure_is_documented() -> None:
    assert "exclude-newer-package" in GETTING_STARTED
    assert "uv lock --upgrade-package" in GETTING_STARTED
    assert "hash-locked" in GETTING_STARTED  # the build-dependency residual is stated


def test_the_setup_block_does_not_follow_develop_with_install() -> None:
    # `uv sync` is exact: `make install` (--no-dev) after `make develop` removes the dev tools.
    block = GETTING_STARTED.split("### 2. Install Dependencies")[1].split("###")[0]
    code = "".join(re.findall(r"```bash\n(.*?)```", block, re.DOTALL))
    uses = [w for w in ("make develop", "make install") if w in code]
    assert len(uses) <= 1, "show develop and install as alternatives, not as a sequence"


def test_the_requirements_targets_are_uv_wrappers_and_pip_compile_is_gone() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    guide = (ROOT / "wiki/developer-guide.md").read_text(encoding="utf-8")
    assert "pip-compile" not in makefile
    assert "pip-compile" not in guide
    export = re.search(r"^export-requirements:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert export
    for flag in (
        "export",
        "--frozen",
        "--no-dev",
        "--no-editable",
        "--no-emit-project",
        "--no-header",
    ):
        assert flag in export.group(1), flag
    assert "requirements-txt" in export.group(1)
    sync = re.search(r"^check-requirements-sync:\n((?:\t.*\n)+)", makefile, re.MULTILINE)
    assert sync
    assert "$(UV) lock --check" in sync.group(1)
    for target in ("export-requirements", "check-requirements-sync"):
        assert target in makefile.split("help::")[1].split("install:")[0]
        assert f"`make {target}`" in guide
    assert "getting-started.md#lock-file-and-the-7-day-rule" in guide
    assert "requirements.txt" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_the_pins_refresh_script_and_dev_runner_prefer_the_pinned_uv() -> None:
    refresh = (ROOT / "scripts/pins_refresh.py").read_text(encoding="utf-8")
    assert '".tools" / "bin" / "uv"' in refresh
    assert ".tools/bin/uv" in (ROOT / "run_backend.sh").read_text(encoding="utf-8")
