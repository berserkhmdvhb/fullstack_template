"""The release workflow: plan, images, licences, VEX, release assets, deploy. Static, offline."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from tests.supply_chain._workflows import ARTIFACT_RETENTION_DAYS, steps_of, workflow

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
    assert set(job["with"]) == {"matrix", "image_tag", "immutable"}


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
    assert {"images", "digests", "grant", "vex"} <= set(job["needs"])
    for needed in ("images", "digests", "grant", "vex"):
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
    assert names == {"sbom-*", "vex", "grant-report", "image-digests"}
    text = _runs(job)
    # The digests and image references outlive the 90-day artifact; the weekly re-scan reads them.
    needles = ("cdx.json", "openvex.json", "license.grant.json", "licence-summary.json")
    for needle in (*needles, "image-digests.env", "scan.env"):
        assert needle in text, needle


def test_vex_waits_for_the_gate_through_success_not_a_status_override() -> None:
    assert "if" not in _jobs()["vex"]  # needs digests and images, both of which must succeed


GH_STUB = """#!/usr/bin/env bash
if [[ "$1" == api ]]; then [[ -n "${API_FAILS:-}" ]] && exit 1; cat "$RELEASES_JSON"; exit 0; fi
echo "gh $*" >> "$CALLS"
"""


def _run_upload(
    tmp_path: Path, releases: list[dict[str, Any]], *, api_fails: bool = False
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
