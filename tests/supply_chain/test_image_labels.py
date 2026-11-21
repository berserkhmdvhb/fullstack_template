"""All three images carry the OCI labels, and the build workflow fills each with a real value."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.supply_chain._workflows import REPO, workflow

DOCKERFILES = ("Dockerfile.backend", "Dockerfile.frontend", "Dockerfile.nginx")
ARGS = {
    "version": "VERSION",
    "revision": "VCS_REF",
    "created": "BUILD_DATE",
    "source": "SOURCE_URL",
}


@pytest.mark.parametrize("name", DOCKERFILES)
def test_each_image_labels_version_revision_created_and_source_from_build_args(name: str) -> None:
    text = (REPO / name).read_text(encoding="utf-8")
    for label, arg in ARGS.items():
        assert re.search(rf"^ARG {arg}=\S+$", text, re.MULTILINE), (name, arg)
        assert f'org.opencontainers.image.{label}="${{{arg}}}"' in text, (name, label)


@pytest.mark.parametrize("name", DOCKERFILES)
def test_the_source_default_is_a_placeholder_the_adapter_renames(name: str) -> None:
    text = (REPO / name).read_text(encoding="utf-8")
    assert "ARG SOURCE_URL=https://github.com/your-org/your-repo\n" in text


def _args_step() -> dict[str, Any]:
    steps = workflow("reusable-build.yml")["jobs"]["build"]["steps"]
    return next(s for s in steps if s.get("id") == "args")


def test_the_build_workflow_passes_the_source_url_of_this_repository_to_every_image() -> None:
    step = _args_step()
    assert step["env"]["SOURCE_URL"] == "${{ github.server_url }}/${{ github.repository }}"
    assert "SOURCE_URL=${SOURCE_URL}" in step["run"]


def test_the_build_workflow_passes_the_commit_sha_and_the_commit_time_not_unknown() -> None:
    step = _args_step()
    assert step["env"]["VCS_REF"] == "${{ github.sha }}"
    run = step["run"]
    assert "VCS_REF=${VCS_REF}" in run
    # The committer time, in UTC and RFC 3339: the same commit always gives the same label.
    assert "git show -s --format=%cd" in run
    assert "--date=format-local:%Y-%m-%dT%H:%M:%SZ" in run
    assert "TZ=UTC" in run
    assert "BUILD_DATE=${created}" in run
    assert "VCS_BRANCH=${VCS_BRANCH}" in run


def test_the_build_arguments_reach_the_build_step_and_expressions_stay_out_of_the_script() -> None:
    wf = workflow("reusable-build.yml")
    build = next(s for s in wf["jobs"]["build"]["steps"] if s.get("id") == "build")
    assert build["with"]["build-args"] == "${{ steps.args.outputs.build_args }}"
    assert "${{" not in _args_step()["run"]


def test_the_manifest_still_supplies_the_version_to_every_image() -> None:
    text = Path(REPO / ".github/container-images.yml").read_text(encoding="utf-8")
    assert text.count('VERSION: "${VERSION}"') == len(DOCKERFILES)
