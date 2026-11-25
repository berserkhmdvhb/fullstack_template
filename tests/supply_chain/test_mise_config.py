"""mise.toml and mise.lock: the developer toolchain. Only the aqua, github and core backends are
on, nothing runs code (no tasks, no hooks, no env files or templates), the lockfile is on, installs
are locked to the two platforms we support, and every tool the mapping moves has a checksum
for each."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from tests.supply_chain._pins import listing

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
    # A tool table may also name its backend: `"name" = { version = "1", backend = "aqua:x/y" }`.
    options = [
        v["backend"]
        for v in CONFIG.get("tools", {}).values()
        if isinstance(v, dict) and "backend" in v
    ]
    # An alias name stands for the backend its alias value names, which is checked as well.
    names = [k for k in CONFIG.get("tools", {}) if k not in CONFIG.get("tool_alias", {})]
    specs = [*names, *aliased, *options]
    for spec in specs:
        # A bare name resolves through the registry, which could pick any backend: not allowed.
        assert backend_of(spec) in ALLOWED, spec


@pytest.mark.parametrize(
    "key", ["tasks", "task_config", "hooks", "plugins", "vars", "monorepo", "env_file", "dotenv"]
)
def test_nothing_that_runs_code_is_configured(key: str) -> None:
    assert key not in CONFIG


def test_env_is_one_relative_path_entry_and_nothing_else() -> None:
    """`.tools/bin` on PATH for people who run `mise activate`. No secret, no file loaded (the app
    reads .env itself; loading it here would read it twice), no template or command that runs."""
    env = CONFIG["env"]
    assert env == {"_": {"path": [".tools/bin"]}}
    text = (ROOT / "mise.toml").read_text()
    section = text.split("\n[env]\n", 1)[1].split("\n[", 1)[0]
    for word in ("file", "source", "dotenv", "{{", "exec", "secret", "token", "KEY", "PASSWORD"):
        assert word not in "".join(ln for ln in section.splitlines() if not ln.startswith("#"))


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


# The 23 rows the mapping moves; the rest of tools/pins.tsv stays (mise itself, Python, the five uv
# tools, the two data rows).
MOVED = {
    "node", "uv", "helm3", "helm4", "kubectl", "kubectl-floor", "kind", "kubeconform", "promtool",
    "ct", "helm-unittest", "actionlint", "hadolint", "gitleaks", "squawk", "oasdiff", "cosign",
    "kyverno", "yq", "bats", "shellcheck", "lychee", "zizmor",
}  # fmt: skip
LOCK = tomllib.loads((ROOT / "mise.lock").read_text())


def test_every_moved_tool_resolves_from_mise_toml_and_not_from_the_pins_file() -> None:
    pins = {
        ln.split("\t")[0]
        for ln in (ROOT / "tools/pins.tsv").read_text().splitlines()
        if ln[:1] != "#" and ln
    }
    assert not MOVED & pins
    seen = listing()
    for name in MOVED:
        version, key = seen[name]
        assert key, name
        assert key in CONFIG["tools"], name
        assert version, name


def test_every_lock_entry_has_a_checksum_for_both_platforms_and_the_pinned_version() -> None:
    assert len(LOCK["tools"]) == len(CONFIG["tools"])
    for key, entries in LOCK["tools"].items():
        assert len(entries) == 1, key
        entry = entries[0]
        for platform in ("linux-x64", "macos-arm64"):
            locked = entry[f"platforms.{platform}"]
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", locked["checksum"]), (key, platform)
            assert locked["url"].startswith("https://"), (key, platform)
        # node is locked under its short name; the config spells its backend.
        wanted = CONFIG["tools"].get(key, CONFIG["tools"].get(f"core:{key}"))
        assert entry["version"] == (wanted["version"] if isinstance(wanted, dict) else wanted), key


def test_the_helm_unittest_binary_keeps_the_name_its_plugin_yaml_runs() -> None:
    """mise strips the platform from the name of a lone binary (untt-linux-amd64 -> untt), which
    plugin.yaml does not know: each locked platform names the file it ships."""
    table = CONFIG["tools"]["github:helm-unittest/helm-unittest"]["platforms"]
    assert table["linux-x64"]["rename_exe"] == "untt-linux-amd64"
    assert table["macos-arm64"]["rename_exe"] == "untt-macos-arm64"


def test_zizmor_is_a_verified_aqua_tool_and_python_is_not_a_mise_tool() -> None:
    assert "aqua:zizmorcore/zizmor" in CONFIG["tools"]
    for platform in ("linux-x64", "macos-arm64"):
        locked = LOCK["tools"]["aqua:zizmorcore/zizmor"][0][f"platforms.{platform}"]
        assert locked["provenance"] == "github-attestations", platform
    # mise's core:python takes the newest python-build-standalone build, which can be hours old;
    # uv's pinned release carries older builds, so Python stays a `uv python install` row.
    assert not [k for k in CONFIG["tools"] if k.removeprefix("core:") == "python"]
    assert "python" in {
        ln.split("\t")[0]
        for ln in (ROOT / "tools/pins.tsv").read_text().splitlines()
        if ln[:1] != "#"
    }


def test_a_version_is_locked_only_if_it_is_at_least_seven_days_old() -> None:
    assert SETTINGS["minimum_release_age"] == "7d"
    assert "minimum_release_age_excludes" not in SETTINGS
