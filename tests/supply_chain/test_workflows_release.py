"""The release workflow: plan, images, licences, VEX, release assets, deploy. Static, offline."""

from __future__ import annotations

import re
from typing import Any, cast

from tests.supply_chain._workflows import ARTIFACT_RETENTION_DAYS, steps_of, workflow

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
    assert {"images", "grant", "vex"} <= set(job["needs"])
    assert "needs.images.result == 'success'" in job["if"]
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


def test_release_assets_upload_the_sbom_vex_and_licence_report() -> None:
    job = _assets()
    names = {
        s["with"].get("name") or s["with"].get("pattern")
        for s in _uses(job, "actions/download-artifact@")
    }
    assert names == {"sbom-*", "vex", "grant-report"}
    text = _runs(job)
    for needle in ("cdx.json", "openvex.json", "license.grant.json", "licence-summary.json"):
        assert needle in text, needle
