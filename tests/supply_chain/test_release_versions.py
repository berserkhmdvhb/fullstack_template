"""Release tags: release-please's tag format, release.yml's trigger, and setuptools_scm's parse.

Offline: setuptools_scm (a dev dependency) runs in a throwaway git repository.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from typing import TYPE_CHECKING, Any

from tests.supply_chain._workflows import REPO, workflow

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

GIT: str = shutil.which("git") or "/usr/bin/git"
SCM_CLI = ("-m", "setuptools_scm", "--root")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _config() -> dict[str, Any]:
    return dict(json.loads((REPO / "release-please-config.json").read_text(encoding="utf-8")))


def _tag(version: str) -> str:
    """The tag release-please creates for a version of the root package, from its config."""
    config = _config()
    assert config.get("include-component-in-tag") is False  # no `<component>-` prefix
    return ("v" if config.get("include-v-in-tag", True) else "") + version


def test_release_please_tracks_one_root_package_with_the_simple_strategy() -> None:
    config = _config()
    assert config["release-type"] == "simple"
    assert config["packages"] == {".": {}}
    manifest = json.loads((REPO / ".release-please-manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"."}
    assert SEMVER.match(manifest["."])
    # The hand-written wiki/changelog.md stays the one changelog.
    assert config["skip-changelog"] is True


def test_release_please_writes_its_version_to_a_file_nothing_reads() -> None:
    """`simple` always writes a version file. VERSION is the image baseline (absent: 0.0.0), so a
    release-written VERSION would give every later main image the last release's version label.
    The tag and the manifest are the version's records; the file release-please writes is inert."""
    name = _config()["version-file"]
    assert name == ".release-please-version"
    readers = subprocess.run(
        [GIT, "grep", "-l", "-F", name, "--", ".", ":!wiki", ":!.superpowers"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    assert sorted(readers) == [
        "release-please-config.json",
        "tests/supply_chain/test_release_versions.py",
    ]


def test_release_please_creates_a_draft_and_still_creates_the_tag() -> None:
    # release-assets uploads to a draft; immutable releases refuse assets once published.
    config = _config()
    assert config["draft"] is True
    assert config["force-tag-creation"] is True  # a draft alone creates no tag, so no release run


def test_the_tag_release_please_creates_triggers_release_and_passes_the_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tag = _tag("1.2.3")
    assert tag == "v1.2.3"
    patterns = workflow("release.yml")["on"]["push"]["tags"]
    assert patterns == ["v*"]
    assert any(re.fullmatch(p.replace("*", ".*"), tag) for p in patterns)
    scripts = str(REPO / ".github" / "scripts")
    sys.path.insert(0, scripts)
    try:
        import container_matrix  # noqa: PLC0415
    finally:
        sys.path.remove(scripts)
    for name, value in (
        ("GITHUB_REF_NAME", tag),
        ("GITHUB_REF_TYPE", "tag"),
        ("GITHUB_SHA", "a" * 40),
    ):
        monkeypatch.setenv(name, value)
    plan = container_matrix.compute_version(REPO)
    assert (plan["version"], plan["immutable"]) == ("1.2.3", "true")


def _git(root: Path, *args: str) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(root.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    done = subprocess.run(
        [GIT, *args], cwd=root, env=env, capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


SCM_PACKAGES = ("setuptools-scm", "vcs-versioning")


def _pins(entries: list[Any]) -> dict[str, str]:
    """`name==version` requirements (strings or uv's `{requirement = ...}` tables) by name."""
    reqs = [e["requirement"] if isinstance(e, dict) else e for e in entries]
    return {r.split("==")[0]: r for r in reqs if r.split("==")[0] in SCM_PACKAGES}


def test_the_dev_group_holds_setuptools_scm_at_the_build_constraint_pins() -> None:
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    build = _pins(project["tool"]["uv"]["build-constraint-dependencies"])
    assert set(build) == set(SCM_PACKAGES)
    assert _pins(project["dependency-groups"]["dev"]) == build  # the version the build uses


def _scm_version(root: Path) -> str:
    """setuptools_scm's version of the checkout, from the dev group: no uv, cache or network."""
    done = subprocess.run(
        [sys.executable, *SCM_CLI, str(root)],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip().splitlines()[-1]


def test_setuptools_scm_reads_the_release_tag_and_versions_a_later_commit_as_dev(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy(REPO / "pyproject.toml", root / "pyproject.toml")  # the real [tool.setuptools_scm]
    _git(root, "init", "-q")
    _git(root, "add", "pyproject.toml")
    _git(root, "commit", "-q", "-m", "chore: init")
    _git(root, "tag", _tag("1.2.3"))
    assert _scm_version(root) == "1.2.3"
    _git(root, "commit", "-q", "--allow-empty", "-m", "fix: after the release")
    sha = _git(root, "rev-parse", "--short", "HEAD")
    version = _scm_version(root)
    assert re.fullmatch(rf"1\.2\.4\.dev1\+g{sha}[0-9a-f]*", version), version
