"""The image structure checks: the PR job in ci.yml, the release digest check, the manifest."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, cast

import pytest
import yaml

from tests.supply_chain._workflows import REPO, workflow

BASH: str = shutil.which("bash") or "/usr/bin/bash"
RELEASE_IF = (
    "github.event_name == 'push' && "
    "(github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v'))"
)
CACHE_WORDS = ("cache-from", "cache-to", "type=gha", "actions/cache")
LABEL_ARGS = ("SOURCE_URL", "VCS_REF", "VCS_BRANCH", "BUILD_DATE")


def _ci() -> dict[str, Any]:
    return cast("dict[str, Any]", workflow("ci.yml")["jobs"])


def _release() -> dict[str, Any]:
    return cast("dict[str, Any]", workflow("reusable-build.yml")["jobs"])


def _text(job: dict[str, Any]) -> str:
    return json.dumps(job)


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def _uses(job: dict[str, Any]) -> list[str]:
    return [str(s["uses"]) for s in job["steps"] if "uses" in s]


def _manifest() -> list[dict[str, Any]]:
    path = REPO / ".github" / "container-images.yml"
    return cast("list[dict[str, Any]]", yaml.safe_load(path.read_text(encoding="utf-8"))["images"])


# --- the manifest ---


def test_every_manifest_image_declares_distroless() -> None:
    for image in _manifest():
        assert isinstance(image.get("distroless"), bool), image["id"]


def test_the_manifest_is_the_distroless_backend_and_frontend() -> None:
    by_id = {i["id"]: i["distroless"] for i in _manifest()}
    assert by_id == {"backend": True, "frontend": True}


# --- the pull request job ---


def test_the_plan_and_the_check_job_exist_and_are_gated_by_ci_ok() -> None:
    needs = _ci()["ci-ok"]["needs"]
    assert "image-plan" in needs
    assert "images" in needs


def test_the_images_job_runs_on_pull_requests_only_and_follows_the_path_filter() -> None:
    cond = _ci()["images"]["if"]
    assert "github.event_name == 'pull_request'" in cond
    assert "needs.changes.outputs.images == 'true'" in cond
    assert set(_ci()["images"]["needs"]) == {"changes", "image-plan"}
    assert "images" in _ci()["changes"]["outputs"]
    assert "push" not in cond


@pytest.mark.parametrize("name", ["image-plan", "images"])
def test_the_jobs_have_read_only_permissions_and_a_timeout(name: str) -> None:
    job = _ci()[name]
    assert job["permissions"] == {"contents": "read"}
    assert "timeout-minutes" in job
    assert "continue-on-error" not in job


def test_the_images_job_never_pushes_logs_in_or_touches_secrets_or_a_cache() -> None:
    job = _ci()["images"]
    text = _text(job)
    assert not any(u.startswith("docker/login-action@") for u in _uses(job))
    assert "secrets." not in text
    assert "GITHUB_TOKEN" not in text
    assert "docker login" not in text
    for word in (
        "push: true",
        "--push",
        "docker push",
        "push=true",
        "type=registry",
        "--output",
        "output:",
    ):
        assert word not in text, word
    for word in CACHE_WORDS:
        assert word not in text, word


def test_the_images_job_builds_locally_and_checks_strictly() -> None:
    runs = _runs(_ci()["images"])
    assert re.search(
        r"docker buildx build\b.*--load|--load\b.*docker buildx build", runs, re.DOTALL
    )
    assert "scripts/ci/image-checks.sh --strict" in runs
    assert '"${IMAGE_ID}"' in runs


def test_the_images_job_passes_the_release_build_arguments_from_the_matrix() -> None:
    job = _ci()["images"]
    step = next(s for s in job["steps"] if "SOURCE_URL" in s.get("env", {}))
    assert step["env"]["SOURCE_URL"] == "${{ github.server_url }}/${{ github.repository }}"
    assert step["env"]["VCS_REF"] == "${{ github.sha }}"
    assert "(.build_args // {})" in step["run"]
    for arg in LABEL_ARGS:
        assert f"{arg}=" in step["run"], arg
    assert "${{" not in step["run"]
    assert "git show -s --format=%cd" in step["run"]


def test_the_plan_job_renders_the_matrix_with_the_helper_and_the_images_job_uses_it() -> None:
    assert "container_matrix.py --matrix" in _runs(_ci()["image-plan"])
    assert (
        _ci()["images"]["strategy"]["matrix"]
        == "${{ fromJSON(needs.image-plan.outputs.platform_matrix) }}"
    )
    assert _ci()["image-plan"]["if"] == _ci()["images"]["if"]


def test_pull_requests_build_linux_amd64_only_whatever_the_variable_says() -> None:
    plan = _ci()["image-plan"]
    step = next(s for s in plan["steps"] if s.get("id") == "plan")
    assert step["env"] == {"BUILD_ARM64": "false"}  # a fixed value, never vars.BUILD_ARM64
    assert "vars." not in _text(plan)
    assert plan["outputs"]["platform_matrix"] == "${{ steps.plan.outputs.platform_matrix }}"
    job = _ci()["images"]
    assert job["runs-on"] == "${{ matrix.runner }}"
    build = next(s for s in job["steps"] if "SOURCE_URL" in s.get("env", {}))
    assert build["env"]["PLATFORM"] == "${{ matrix.platform }}"
    assert '--platform "${PLATFORM}"' in build["run"]
    assert "vars." not in _text(job)


def test_every_new_third_party_action_is_pinned() -> None:
    for job in (_ci()["image-plan"], _ci()["images"]):
        for use in _uses(job):
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", use), use
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"actions/checkout@[0-9a-f]{40} # v\d", text)


# --- the gate semantics ---


def _gate(results: dict[str, str], event: str) -> int:
    script = _ci()["ci-ok"]["steps"][-1]["run"]
    needs = json.dumps({k: {"result": v, "outputs": {}} for k, v in results.items()})
    env = {"NEEDS": needs, "EVENT_NAME": event, "PATH": os.environ["PATH"]}
    return subprocess.run(
        [BASH, "-c", script], env=env, capture_output=True, check=False
    ).returncode


def _all(**override: str) -> dict[str, str]:
    return dict.fromkeys(set(_ci()) - {"ci-ok"}, "success") | override


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is needed to run the gate script")
class TestGate:
    @pytest.mark.parametrize("event", ["pull_request", "push"])
    def test_a_skipped_check_job_passes_where_it_is_skipped_by_design(self, event: str) -> None:
        assert _gate(_all(images="skipped", **{"image-plan": "skipped"}), event) == 0

    @pytest.mark.parametrize("job", ["images", "image-plan"])
    @pytest.mark.parametrize("bad", ["failure", "cancelled"])
    @pytest.mark.parametrize("event", ["pull_request", "push"])
    def test_a_failed_check_job_blocks(self, job: str, bad: str, event: str) -> None:
        assert _gate(_all(**{job: bad}), event) != 0


# --- the release check ---


def test_the_release_check_pulls_by_digest_and_checks_strictly() -> None:
    job = _release()["image-checks"]
    runs = _runs(job)
    assert job["needs"] == "build"
    assert job["if"] == RELEASE_IF
    assert job["runs-on"] == "${{ matrix.runner }}"  # the container runs natively, per platform
    assert job["strategy"]["matrix"] == "${{ fromJSON(inputs.platform_matrix) }}"
    assert '"${REPOSITORY}@${DIGEST}"' in runs
    assert "docker pull" in runs
    assert "scripts/ci/image-checks.sh --strict" in runs
    assert "IMAGE_REF" not in runs
    assert ":${{" not in runs
    assert "${{" not in runs


def test_the_release_check_logs_in_through_the_action_input_with_read_only_packages() -> None:
    job = _release()["image-checks"]
    assert job["permissions"] == {"contents": "read", "packages": "read"}
    assert "timeout-minutes" in job
    login = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("docker/login-action@")
    )
    assert login["with"]["password"] == "${{ secrets.GITHUB_TOKEN }}"  # noqa: S105 — an expression
    assert "secrets." not in _runs(job)
    assert "packages: write" not in json.dumps(job)


def test_the_release_check_has_the_digest_from_the_build_artifact() -> None:
    job = _release()["image-checks"]
    assert any(
        str(s.get("uses", "")).startswith("actions/download-artifact@")
        and s["with"]["name"] == "platform-${{ matrix.id }}.${{ matrix.slug }}"
        for s in job["steps"]
    )
    assert "sha256:[0-9a-f]{64}" in _runs(job)


def test_signing_needs_the_image_checks() -> None:
    needs = _release()["sign-attest"]["needs"]
    assert "image-checks" in needs
    assert {"build", "sbom", "grype"} <= set(needs)
