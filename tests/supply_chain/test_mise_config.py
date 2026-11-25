"""mise.toml: settings only. Only the aqua, github and core backends are on, nothing runs code
(no tasks, no hooks, no env templates), the lockfile is on and installs are locked to the two
platforms we support."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = tomllib.loads((ROOT / "mise.toml").read_text())
ALLOWED = {"aqua", "github", "core"}
# Every backend mise 2026.11 knows (`mise backends ls`, plus the pipx spelling of pypi).
ALL_BACKENDS = {
    "aqua", "asdf", "cargo", "conda", "core", "dotnet", "forgejo", "gem", "github", "gitlab", "go",
    "http", "npm", "packslip", "pipx", "pypi", "s3", "spinel", "spm", "ubi", "vfox",
}  # fmt: skip
SETTINGS = CONFIG.get("settings", {})


def backend_of(spec: str) -> str:
    return spec.split(":", 1)[0] if ":" in spec else "registry"


def test_every_backend_but_the_allowed_ones_is_disabled() -> None:
    assert set(SETTINGS["disable_backends"]) == ALL_BACKENDS - ALLOWED


def test_the_tools_and_aliases_name_only_allowed_backends() -> None:
    aliased = [v for v in CONFIG.get("tool_alias", {}).values() if isinstance(v, str)]
    specs = [*CONFIG.get("tools", {}), *aliased]
    for spec in specs:
        # A bare name resolves through the registry, which could pick any backend: not allowed.
        assert backend_of(spec) in ALLOWED, spec


@pytest.mark.parametrize(
    "key", ["tasks", "task_config", "hooks", "env", "plugins", "vars", "monorepo"]
)
def test_nothing_that_runs_code_is_configured(key: str) -> None:
    assert key not in CONFIG


def test_the_lockfile_is_on_and_installs_are_locked() -> None:
    assert SETTINGS["lockfile"] is True
    assert SETTINGS["locked"] is True


def test_the_two_supported_platforms_are_locked() -> None:
    assert sorted(SETTINGS["lockfile_platforms"]) == ["linux-x64", "macos-arm64"]


def test_release_data_comes_from_the_source_and_no_usage_is_reported() -> None:
    assert SETTINGS["use_versions_host"] is False
    assert SETTINGS["use_versions_host_track"] is False


def test_the_download_checks_stay_on() -> None:
    for name in ("github_attestations", "slsa"):
        assert SETTINGS[name] is True, name
    for name in ("cosign", "minisign", "slsa", "github_attestations"):
        assert SETTINGS["aqua"][name] is True, name
