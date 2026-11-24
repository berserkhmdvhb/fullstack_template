"""The optional devcontainer: pinned by digest, tools from the installer, seen by Renovate."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from tests.supply_chain.test_renovate_config import renovate

ROOT = Path(__file__).resolve().parent.parent.parent
DEVCONTAINER = ROOT / ".devcontainer" / "devcontainer.json"
DIGEST = r"@sha256:[0-9a-f]{64}"
# The base image's release name and the GitHub runner of the same OS.
RUNNER_OF = {"noble": "ubuntu-24.04"}


def devcontainer() -> dict[str, Any]:
    """JSONC with whole-line comments only, so the standard library can read it."""
    text = DEVCONTAINER.read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))
    return data


def test_the_image_and_every_feature_are_pinned_by_digest() -> None:
    cfg = devcontainer()
    assert re.fullmatch(rf"[a-z0-9.-]+(/[\w.-]+)+:[\w.-]+{DIGEST}", cfg["image"]), cfg["image"]
    assert "build" not in cfg
    assert "dockerFile" not in cfg
    assert cfg["features"]
    for ref, options in cfg["features"].items():
        assert re.fullmatch(rf"ghcr\.io/devcontainers/features/[a-z-]+{DIGEST}", ref), ref
        assert "version" not in options, ref


def test_kind_gets_a_docker_daemon() -> None:
    features = [ref.split("@")[0].rsplit("/", 1)[1] for ref in devcontainer()["features"]]
    assert features == ["docker-in-docker"]


def test_no_tool_version_is_restated() -> None:
    cfg = devcontainer()
    rest = {k: v for k, v in cfg.items() if k != "image"}
    assert not re.search(r"\d+\.\d+", json.dumps(rest)), "versions belong in tools/pins.tsv"
    raw = DEVCONTAINER.read_text(encoding="utf-8")
    for row in (ln for ln in (ROOT / "tools" / "pins.tsv").read_text().splitlines()):
        if row and not row.startswith("#"):
            name, version = row.split("\t")[:2]
            assert not re.search(rf"(?<![\w.]){re.escape(version)}(?![\w.])", raw), name
    assert not any(re.search(r"/(python|node)\b", ref) for ref in cfg["features"])


def test_post_create_runs_the_pinned_installer() -> None:
    cfg = devcontainer()
    assert cfg["postCreateCommand"].endswith("&& make tools")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^tools:\n\t@scripts/tools\.sh install\n", makefile, re.MULTILINE)
    assert cfg["remoteEnv"]["PATH"].startswith("${containerWorkspaceFolder}/.tools/bin:")


def test_the_base_image_is_the_kind_jobs_runner_os() -> None:
    release = devcontainer()["image"].split("@")[0].rsplit("-", 1)[1]
    kind = yaml.safe_load(
        (ROOT / ".github/workflows/reusable-kind.yml").read_text(encoding="utf-8")
    )
    runners = {job["runs-on"] for job in kind["jobs"].values()}
    assert runners == {RUNNER_OF[release]}


def test_renovate_updates_the_devcontainer() -> None:
    cfg = renovate()
    assert cfg["devcontainer"]["enabled"] is True
    assert "enabledManagers" not in cfg
    assert "docker:pinDigests" in cfg["extends"]
    for rule in cfg["packageRules"]:
        assert not (
            "devcontainer" in rule.get("matchManagers", []) and rule.get("enabled") is False
        )
