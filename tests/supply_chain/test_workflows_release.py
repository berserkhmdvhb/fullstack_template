"""The release workflow: plan, images, licences, VEX, release assets, deploy. Static, offline."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from tests.supply_chain._workflows import (
    ARTIFACT_RETENTION_DAYS,
    REPO,
    WORKFLOWS,
    steps_of,
    workflow,
)

BASH: str = shutil.which("bash") or "/usr/bin/bash"
MIN_UPLOAD_STEPS = 3  # digests, grant and vex


def _wf() -> dict[str, Any]:
    return workflow("release.yml")


def _jobs() -> dict[str, Any]:
    """The jobs with steps; the `deploy` caller job is covered by test_workflows_deploy.py."""
    return {n: j for n, j in _wf()["jobs"].items() if "uses" not in j}


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def _uses(job: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    return [s for s in job["steps"] if str(s.get("uses", "")).startswith(prefix)]


def test_triggers_are_push_to_main_and_v_tags_only() -> None:
    on = _wf()["on"]
    assert set(on) == {"push"}
    assert on["push"] == {"branches": ["main"], "tags": ["v*"]}


def test_release_is_never_cancelled_mid_push() -> None:
    conc = _wf()["concurrency"]
    assert conc["cancel-in-progress"] is False
    assert conc["group"] == "release-${{ github.ref }}"


def test_top_level_permissions_are_empty() -> None:
    assert _wf()["permissions"] == {}


def test_images_job_calls_the_reusable_build_with_the_union_of_its_permissions() -> None:
    job = _wf()["jobs"]["images"]
    assert job["uses"] == "./.github/workflows/reusable-build.yml"
    assert job["needs"] == "plan"
    assert job["permissions"] == {
        "contents": "read",
        "packages": "write",
        "security-events": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert set(job["with"]) == {"matrix", "platform_matrix", "image_tag", "immutable"}
    assert job["with"]["platform_matrix"] == "${{ needs.plan.outputs.platform_matrix }}"


def test_step_running_jobs_hold_no_registry_or_signing_grant() -> None:
    perms = {n: j["permissions"] for n, j in _jobs().items()}
    assert perms.pop("release-assets") == {"contents": "write"}
    assert all(p == {"contents": "read"} for p in perms.values()), perms
    for job in _jobs().values():
        assert not _uses(job, "docker/build-push-action@")
        assert not _uses(job, "actions/attest")
        assert "cosign sign" not in _runs(job)


def test_jobs_run_in_order() -> None:
    jobs = _wf()["jobs"]
    assert jobs["images"]["needs"] == "plan"
    assert jobs["digests"]["needs"] == ["plan", "images"]
    assert jobs["grant"]["needs"] == "images"
    assert jobs["vex"]["needs"] == ["digests", "images"]
    assert jobs["deploy"]["needs"] == ["plan", "digests", "images"]


def test_plan_job_uses_the_matrix_helper_and_exposes_outputs() -> None:
    job = _jobs()["plan"]
    assert ".github/scripts/container_matrix.py --matrix" in _runs(job)
    assert {"matrix", "version", "channel", "image_tag", "immutable"} <= set(job["outputs"])
    assert _wf()["jobs"]["images"]["with"]["matrix"] == "${{ needs.plan.outputs.matrix }}"


def test_digests_job_writes_and_uploads_image_digests_env() -> None:
    job = _jobs()["digests"]
    assert "image-digests.env" in _runs(job)
    assert any(s["with"]["name"] == "image-digests" for s in _uses(job, "actions/upload-artifact@"))


def test_grant_and_vex_are_advisory() -> None:
    for name, script in (("grant", "scan-licenses.sh"), ("vex", "generate-vex.sh")):
        job = _jobs()[name]
        text = _runs(job)
        assert script in text, name
        assert "::warning" in text, name
        assert "GITHUB_STEP_SUMMARY" in text, name
        assert "security-events" not in job["permissions"], name


def test_every_upload_keeps_90_days() -> None:
    seen = 0
    for job in _jobs().values():
        for step in _uses(job, "actions/upload-artifact@"):
            assert step["with"]["retention-days"] == ARTIFACT_RETENTION_DAYS
            seen += 1
    assert seen >= MIN_UPLOAD_STEPS


def test_every_job_writes_a_step_summary_and_has_a_timeout() -> None:
    for name, job in _jobs().items():
        assert isinstance(job["timeout-minutes"], int), name
        assert "GITHUB_STEP_SUMMARY" in _runs(job), name


def test_no_pull_request_trigger_anywhere_in_the_workflow() -> None:
    for step in steps_of(_wf()):
        assert "pull_request" not in step.get("if", "")
    assert "pull_request" not in _wf()["on"]
    assert "workflow_dispatch" not in _wf()["on"]


def test_nothing_hard_codes_an_owner() -> None:
    assert "ghcr.io/" not in str(_wf()).replace("ghcr.io/${{", "")


TAG_ONLY = "startsWith(github.ref, 'refs/tags/v')"


def _assets() -> dict[str, Any]:
    return cast("dict[str, Any]", _jobs()["release-assets"])


def test_release_assets_run_only_on_v_tags_after_the_images_job() -> None:
    job = _assets()
    assert TAG_ONLY in job["if"]
    assert {"images", "digests", "grant", "vex", "chart"} <= set(job["needs"])
    for needed in ("images", "digests", "grant", "vex", "chart"):
        assert f"needs.{needed}.result == 'success'" in job["if"]
    assert job["permissions"] == {"contents": "write"}
    assert "id-token" not in str(job["permissions"])


def test_release_assets_only_ever_create_or_extend_a_draft() -> None:
    job = _assets()
    text = _runs(job)
    assert "gh release create" in text
    assert "--draft" in text
    assert "--verify-tag" in text
    assert "gh release upload" in text
    assert "gh release edit" not in text
    assert "--draft=false" not in text
    assert "--latest" not in text
    assert "::error::" in text  # a published release is refused with a message
    assert not re.search(r"\$\{\{", text)


def test_release_assets_upload_the_sbom_vex_licence_report_and_the_digests() -> None:
    job = _assets()
    names = {
        s["with"].get("name") or s["with"].get("pattern")
        for s in _uses(job, "actions/download-artifact@")
    }
    assert names == {"sbom-*", "vex", "grant-report", "image-digests", "chart"}
    text = _runs(job)
    # The digests and image references outlive the 90-day artifact; the weekly re-scan reads them.
    needles = ("cdx.json", "openvex.json", "license.grant.json", "licence-summary.json")
    for needle in (*needles, "image-digests.env", "scan.env", "chart.env", "provenance.md"):
        assert needle in text, needle


def test_vex_waits_for_the_gate_through_success_not_a_status_override() -> None:
    assert "if" not in _jobs()["vex"]  # needs digests and images, both of which must succeed


GH_STUB = """#!/usr/bin/env bash
if [[ "$1" == api ]]; then [[ -n "${API_FAILS:-}" ]] && exit 1; cat "$RELEASES_JSON"; exit 0; fi
echo "gh $*" >> "$CALLS"
"""


def _run_upload(
    tmp_path: Path,
    releases: list[dict[str, Any]],
    *,
    api_fails: bool = False,
    prerelease: str = "false",
) -> tuple[int, str, str]:
    step = next(s for s in _assets()["steps"] if s.get("name") == "Upload to the draft release")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)
    (tmp_path / "release-assets").mkdir()
    (tmp_path / "release-assets" / "a.json").write_text("{}")
    (tmp_path / "releases.json").write_text(json.dumps(releases))
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "RELEASES_JSON": str(tmp_path / "releases.json"),
        "CALLS": str(tmp_path / "calls"),
        "GH_REPO": "o/r",
        "TAG": "v1.0.0",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "API_FAILS": "1" if api_fails else "",
        "PRERELEASE": prerelease,
    }
    done = subprocess.run(
        [BASH, "-e", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    return done.returncode, done.stdout, calls


def _rel(tag: str, *, draft: bool) -> dict[str, Any]:
    return {"tag_name": tag, "draft": draft}


def test_upload_creates_a_draft_when_there_is_no_release_for_the_tag(tmp_path: Path) -> None:
    code, _, calls = _run_upload(tmp_path, [_rel("v0.9.0", draft=False)])
    assert code == 0
    assert "gh release create v1.0.0 --draft --verify-tag" in calls
    assert "gh release upload v1.0.0" in calls


@pytest.mark.parametrize(("prerelease", "flagged"), [("true", True), ("false", False), ("", False)])
def test_only_a_pre_release_tag_creates_a_pre_release_draft(
    tmp_path: Path, prerelease: str, *, flagged: bool
) -> None:
    code, _, calls = _run_upload(tmp_path, [], prerelease=prerelease)
    assert code == 0
    (create,) = [c for c in calls.splitlines() if c.startswith("gh release create")]
    assert ("--prerelease" in create.split()) is flagged
    assert _assets()["env"]["PRERELEASE"] == "${{ needs.plan.outputs.prerelease }}"
    assert "plan" in _assets()["needs"]


def test_upload_extends_the_single_draft_without_creating_another(tmp_path: Path) -> None:
    code, _, calls = _run_upload(tmp_path, [_rel("v1.0.0", draft=True)])
    assert code == 0
    assert "gh release create" not in calls
    assert "gh release upload v1.0.0" in calls


@pytest.mark.parametrize(
    "releases",
    [
        [_rel("v1.0.0", draft=False)],
        [
            _rel("v1.0.0", draft=True),
            _rel("v1.0.0", draft=False),
        ],  # a draft next to a published one
        [_rel("v1.0.0", draft=False), _rel("v1.0.0", draft=True)],
        [_rel("v1.0.0", draft=True), _rel("v1.0.0", draft=True)],  # two drafts: ambiguous
    ],
)
def test_upload_refuses_a_published_or_ambiguous_release(
    tmp_path: Path, releases: list[dict[str, Any]]
) -> None:
    code, out, calls = _run_upload(tmp_path, releases)
    assert code != 0
    assert "::error::" in out
    assert "gh release upload" not in calls
    assert "gh release create" not in calls


def test_a_failing_release_listing_fails_the_step_instead_of_looking_like_no_release(
    tmp_path: Path,
) -> None:
    code, _, calls = _run_upload(tmp_path, [], api_fails=True)
    assert code != 0
    assert "gh release create" not in calls
    assert "gh release upload" not in calls


def test_steps_that_pipe_commands_fail_on_the_first_failing_stage() -> None:
    # The default step shell is `bash -e`, which does not set pipefail.
    merge = next(s for s in _jobs()["digests"]["steps"] if s.get("id") == "merge")
    upload = next(s for s in _assets()["steps"] if s.get("name") == "Upload to the draft release")
    for step in (merge, upload):
        assert step["run"].lstrip().startswith("set -euo pipefail"), step.get("name")


def test_no_comment_sits_at_column_zero_inside_the_jobs_mapping() -> None:
    lines = (WORKFLOWS / "release.yml").read_text(encoding="utf-8").splitlines()
    start = lines.index("jobs:")
    assert not [ln for ln in lines[start:] if ln.startswith("#")]


def _fake_python(tmp_path: Path) -> None:
    """The plan step runs $RUNNER_TEMP/pyyaml/bin/python; a wrapper keeps the venv's PyYAML."""
    python = tmp_path / "pyyaml" / "bin" / "python"
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)


def _plan_summary(tmp_path: Path, switch: str) -> str:
    """Run the plan step for real (the helper is the repo's) and return its step summary."""
    step = next(s for s in _jobs()["plan"]["steps"] if s.get("id") == "plan")
    (tmp_path / "pyyaml" / "bin").mkdir(parents=True)
    _fake_python(tmp_path)
    summary, out = tmp_path / "summary.md", tmp_path / "out"
    subprocess.run(
        [BASH, "-c", step["run"]],
        cwd=REPO,
        env={
            "PATH": os.environ["PATH"],
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_REF_NAME": "main",
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
            "GITHUB_REPOSITORY_OWNER": "o",
            "GITHUB_REPOSITORY": "o/r",
            "BUILD_ARM64": switch,
        },
        capture_output=True,
        text=True,
        check=True,
    )
    return summary.read_text()


def test_the_plan_takes_the_arm64_switch_from_the_repository_variable() -> None:
    job = _jobs()["plan"]
    step = next(s for s in job["steps"] if s.get("id") == "plan")
    assert step["env"] == {"BUILD_ARM64": "${{ vars.BUILD_ARM64 }}"}
    assert {"platform_matrix", "arm64"} <= set(job["outputs"])
    assert "${{" not in step["run"]


def test_the_plan_summary_follows_the_helpers_arm64_output(tmp_path: Path) -> None:
    assert "arm64 skipped" in _plan_summary(tmp_path / "unset", "")
    assert "arm64 skipped" not in _plan_summary(tmp_path / "set", "true")
    assert "built natively" in _plan_summary(tmp_path / "set2", "true")


def test_the_plan_says_why_arm64_is_skipped_when_the_switch_is_on_but_no_image_lists_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    shutil.copytree(
        REPO / ".github", root / ".github", ignore=shutil.ignore_patterns("__pycache__")
    )
    manifest = root / ".github" / "container-images.yml"
    text = manifest.read_text(encoding="utf-8").replace(
        "platforms: [linux/amd64, linux/arm64]\n", ""
    )
    manifest.write_text(text, encoding="utf-8")
    step = next(s for s in _jobs()["plan"]["steps"] if s.get("id") == "plan")
    (tmp_path / "pyyaml" / "bin").mkdir(parents=True)
    _fake_python(tmp_path)
    summary = tmp_path / "summary.md"
    env = {
        "PATH": os.environ["PATH"],
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(tmp_path / "out"),
        "GITHUB_STEP_SUMMARY": str(summary),
        "GITHUB_REF_NAME": "main",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
        "GITHUB_REPOSITORY_OWNER": "o",
        "GITHUB_REPOSITORY": "o/r",
        "BUILD_ARM64": "true",
    }
    subprocess.run([BASH, "-c", step["run"]], cwd=root, env=env, check=True, capture_output=True)
    text = summary.read_text()
    assert "arm64 skipped" in text
    assert "no image lists" in text


def _gather(job: str, tmp_path: Path) -> list[str]:
    """Run the SBOM gather step on artifacts that also hold a second SBOM; return the file names."""
    step = next(s for s in _jobs()[job]["steps"] if "cdx.json" in s.get("run", ""))
    arts = tmp_path / ("sbom-artifacts" if job == "grant" else "assets/sbom")
    for art in ("sbom-app.linux-amd64", "sbom-app.linux-arm64"):
        (arts / art).mkdir(parents=True)
        (arts / art / "app.image.sbom.cdx.json").write_text(art)
        (arts / art / "other.image.sbom.cdx.json").write_text("other")
        (arts / art / "project.sbom.cdx.json").write_text("project")
    for rel in (
        "assets/vex/openvex.json",
        "assets/grant/a.license.grant.json",
        "assets/grant/summary.json",
        "assets/digests/image-digests.env",
        "assets/digests/scan.env",
        "assets/chart/chart.env",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    run = step["run"].replace("${SEC_OUTPUT_DIR}", "out/security")
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(tmp_path / "summary")}
    subprocess.run([BASH, "-c", run], cwd=tmp_path, env=env, check=True, capture_output=True)
    where = tmp_path / ("out/security/sbom" if job == "grant" else "release-assets")
    names = sorted(p.name for p in where.glob("*.cdx.json"))
    for name in names:  # each file is the image SBOM of its own platform, never overwritten
        assert (where / name).read_text() == "sbom-" + name.removesuffix(".image.sbom.cdx.json")
    return names


@pytest.mark.parametrize("job", ["grant", "release-assets"])
def test_sbom_gathering_keeps_one_distinct_file_per_image_and_platform(
    job: str, tmp_path: Path
) -> None:
    assert _gather(job, tmp_path) == [
        "app.linux-amd64.image.sbom.cdx.json",
        "app.linux-arm64.image.sbom.cdx.json",
    ]


def test_vex_merges_the_per_platform_grype_reports_per_image() -> None:
    job = _jobs()["vex"]
    (down,) = [
        s for s in _uses(job, "actions/download-artifact@") if s["with"].get("pattern") == "grype-*"
    ]
    assert "merge-multiple" not in down["with"]
    runs = _runs(job)
    assert runs.index("unique_by") < runs.index("generate-vex.sh")


ATTEST_GUARD = (
    "${{ github.event.repository.visibility == 'public' || vars.ATTESTATIONS_ENABLED == 'true' }}"
)


def test_provenance_md_follows_the_attestation_guard_of_the_build() -> None:
    step = next(s for s in _assets()["steps"] if s.get("name") == "Collect the assets")
    assert step["env"] == {"ATTESTED": ATTEST_GUARD}
    guards = {
        s["if"]
        for name in ("reusable-build.yml", "reusable-chart-release.yml")
        for j in workflow(name)["jobs"].values()
        for s in j["steps"]
        if str(s.get("uses", "")).startswith("actions/attest")
    }
    assert guards == {ATTEST_GUARD}


@pytest.mark.parametrize("attested", [True, False])
def test_provenance_md_lists_every_signed_digest_and_how_to_verify_it(
    tmp_path: Path,
    attested: bool,  # noqa: FBT001
) -> None:
    step = next(s for s in _assets()["steps"] if s.get("name") == "Collect the assets")
    files = {
        "assets/sbom/sbom-backend.linux-amd64/backend.image.sbom.cdx.json": "{}",
        "assets/vex/openvex.json": "{}",
        "assets/grant/summary.json": "{}",
        "assets/digests/image-digests.env": (
            f"BACKEND_IMAGE_DIGEST={D1}\nFRONTEND_IMAGE_DIGEST={D2}\n"
        ),
        "assets/digests/scan.env": (
            "BACKEND_IMAGE=ghcr.io/o/r/backend:1.0.0-prod-abc1234\n"
            "FRONTEND_IMAGE=ghcr.io/o/r/frontend:1.0.0-prod-abc1234\n"
        ),
        "assets/chart/chart.env": (
            f"CHART=ghcr.io/o/r/charts/c\nCHART_VERSION=1.0.0\nCHART_DIGEST={D3}\n"
        ),
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    (tmp_path / "assets/grant/backend.license.grant.json").write_text("{}")
    env = {
        "PATH": "/usr/bin:/bin",
        "GH_REPO": "o/r",
        "TAG": "v1.0.0",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "ATTESTED": "true" if attested else "false",
    }
    subprocess.run(
        [BASH, "-e", "-c", step["run"]], cwd=tmp_path, env=env, check=True, capture_output=True
    )
    text = (tmp_path / "release-assets/provenance.md").read_text()
    assert f"- image `ghcr.io/o/r/backend@{D1}`" in text
    assert f"- image `ghcr.io/o/r/frontend@{D2}`" in text
    assert f"- chart `ghcr.io/o/r/charts/c@{D3}` (version 1.0.0)" in text
    # Attestation links and their verify command only when the build attested.
    assert ("https://github.com/o/r/attestations" in text) is attested
    assert ("gh attestation verify" in text) is attested
    assert ("attested by" in text) is attested
    assert ("Not attested" in text) is not attested
    assert (
        "--certificate-identity https://github.com/o/r/.github/workflows/reusable-build.yml@refs/tags/v1.0.0"
        in text
    )
    assert "--certificate-github-workflow-repository o/r" in text
    # The chart is signed by its own workflow: its verify commands name that identity, with its ref.
    chart = text.split("Verify the chart:", 1)[1]
    assert (
        f"cosign verify ghcr.io/o/r/charts/c@{D3} --certificate-identity "
        "https://github.com/o/r/.github/workflows/reusable-chart-release.yml@refs/tags/v1.0.0"
    ) in chart
    assert "reusable-build.yml" not in chart
    assert (
        f"gh attestation verify oci://ghcr.io/o/r/charts/c@{D3} --repo o/r --signer-workflow "
        "o/r/.github/workflows/reusable-chart-release.yml" in chart
    ) is attested
    assert (tmp_path / "release-assets/chart.env").exists()
    assert "3 signed digest(s)" in (tmp_path / "summary").read_text()


D1, D2, D3 = ("sha256:" + c * 64 for c in "123")
