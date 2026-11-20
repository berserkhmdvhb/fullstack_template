"""The release workflow: build, push, verify, SBOM, scan (Task 1.5). Static, offline."""

from __future__ import annotations

import re
from typing import Any

from tests.supply_chain._workflows import steps_of, workflow

ARTIFACT_RETENTION_DAYS = 90
MIN_UPLOAD_STEPS = 4  # one per scan job


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


def test_packages_write_only_on_the_build_job() -> None:
    wide = {
        n: j["permissions"]
        for n, j in _jobs().items()
        if j["permissions"].get("packages") == "write"
    }
    assert wide == {"build": {"contents": "read", "packages": "write"}}


def test_security_events_write_only_on_the_grype_job() -> None:
    wide = {
        n: j["permissions"] for n, j in _jobs().items() if "security-events" in j["permissions"]
    }
    assert wide == {"grype": {"contents": "read", "security-events": "write"}}


def test_packages_read_only_on_the_sbom_job() -> None:
    reads = {n for n, j in _jobs().items() if j["permissions"].get("packages") == "read"}
    assert reads == {"sbom"}
    assert _jobs()["sbom"]["permissions"] == {"contents": "read", "packages": "read"}


def test_no_other_permission_is_widened() -> None:
    for name, job in _jobs().items():
        extra = set(job["permissions"]) - {"contents", "packages", "security-events"}
        assert not extra, name
        assert job["permissions"]["contents"] == "read", name


def test_sbom_is_its_own_job_scanning_by_digest_and_grype_needs_no_registry() -> None:
    jobs = _jobs()
    assert jobs["sbom"]["needs"] == ["plan", "build"]
    assert "infra/security/sbom-generate.sh" in _runs(jobs["sbom"])
    assert "infra/security/sbom-generate.sh" not in _runs(jobs["build"])
    assert not _uses(jobs["build"], "actions/upload-artifact@")[1:]  # only the digest upload
    assert not _uses(jobs["grype"], "docker/login-action@")
    assert "sbom" in jobs["grype"]["needs"]


def test_jobs_run_in_order() -> None:
    jobs = _jobs()
    assert "plan" in jobs
    assert jobs["build"]["needs"] == "plan"
    assert "build" in jobs["digests"]["needs"]
    assert "sbom" in jobs["grype"]["needs"]
    assert "sbom" in jobs["grant"]["needs"]
    assert "grype" in jobs["vex"]["needs"]


def test_plan_job_uses_the_matrix_helper_and_exposes_outputs() -> None:
    job = _jobs()["plan"]
    assert "python3 .github/scripts/container_matrix.py --matrix" in _runs(job)
    assert {"matrix", "version", "channel", "image_tag", "immutable"} <= set(job["outputs"])
    assert "fromJSON(needs.plan.outputs.matrix)" in str(_jobs()["build"]["strategy"]["matrix"])


def test_build_uses_buildx_login_and_gha_cache() -> None:
    job = _jobs()["build"]
    for prefix in (
        "docker/setup-buildx-action@",
        "docker/login-action@",
        "docker/build-push-action@",
    ):
        assert len(_uses(job, prefix)) == 1, prefix
    build = _uses(job, "docker/build-push-action@")[0]["with"]
    assert build["push"] is True
    assert build["cache-from"] == "type=gha,scope=${{ matrix.id }}"
    assert build["cache-to"] == "type=gha,mode=max,scope=${{ matrix.id }}"


def test_login_token_goes_through_with_not_run() -> None:
    for job in _jobs().values():
        login = _uses(job, "docker/login-action@")
        for step in login:
            assert step["with"]["registry"] == "ghcr.io"
            assert step["with"]["username"] == "${{ github.actor }}"
            assert step["with"]["password"] == "${{ secrets.GITHUB_TOKEN }}"  # noqa: S105 — a workflow expression, not a credential
        assert not re.search(r"secrets\.|GITHUB_TOKEN|docker login", _runs(job)), job


def test_digest_verify_runs_after_the_push() -> None:
    steps = _jobs()["build"]["steps"]
    push = next(
        i
        for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith("docker/build-push-action@")
    )
    verify = next(i for i, s in enumerate(steps) if "imagetools inspect" in s.get("run", ""))
    assert verify > push
    run = steps[verify]["run"]
    assert "steps." not in run  # the build digest comes in through env, never inlined
    assert "exit 1" in run
    assert steps[verify]["env"] == {"BUILD_DIGEST": "${{ steps.build.outputs.digest }}"}
    # The registry's digest (imagetools) is compared with the build's, and a mismatch stops the job.
    assert "docker buildx imagetools inspect" in run
    assert "{{.Manifest.Digest}}" in run
    assert '"${resolved}" != "${BUILD_DIGEST}"' in run


def test_latest_only_on_immutable_releases_and_the_versioned_tag_always() -> None:
    job = _jobs()["build"]
    assert job["env"]["IMAGE_REF"] == "${{ matrix.repository }}:${{ needs.plan.outputs.image_tag }}"
    build = _uses(job, "docker/build-push-action@")[0]["with"]
    tags = build["tags"].split("\n")
    tags = [t.strip() for t in tags if t.strip()]
    assert tags[0] == "${{ env.IMAGE_REF }}", "the image_tag tag is unconditional"
    (latest,) = tags[1:]
    assert latest.count(":latest") == 1
    assert latest.startswith("${{ needs.plan.outputs.immutable == 'true' && ")
    assert latest.endswith("|| '' }}")


def test_digests_job_writes_and_uploads_image_digests_env() -> None:
    job = _jobs()["digests"]
    assert "image-digests.env" in _runs(job)
    assert any(s["with"]["name"] == "image-digests" for s in _uses(job, "actions/upload-artifact@"))


def test_grype_is_blocking_and_does_not_restate_fail_level() -> None:
    job = _jobs()["grype"]
    assert "continue-on-error" not in job
    gate = [s for s in job["steps"] if "scan-vulnerabilities.sh" in s.get("run", "")]
    assert len(gate) == 1
    assert "continue-on-error" not in gate[0]
    text = _runs(job)
    assert "infra/security/scan-vulnerabilities.sh" in text
    assert "infra/security/tools-install.sh grype" in text
    assert not re.search(r"fail[-_]level|FAIL_LEVEL|--fail-on|\s-l\s", text)
    raw = str(job)
    assert not re.search(r"\b(high|critical)\b", raw.lower().replace("security-events", ""))


def test_grype_uploads_sarif_even_when_grype_fails() -> None:
    job = _jobs()["grype"]
    (sarif,) = _uses(job, "github/codeql-action/upload-sarif@")
    assert sarif["if"] == "always()"
    assert sarif["with"]["category"] == "grype-${{ matrix.id }}"
    assert sarif["continue-on-error"] is True
    warn = [s for s in job["steps"] if "steps.sarif.outcome == 'failure'" in str(s.get("if", ""))]
    assert len(warn) == 1
    assert "::warning" in warn[0]["run"]
    assert "GITHUB_STEP_SUMMARY" in warn[0]["run"]
    artifacts = list(_uses(job, "actions/upload-artifact@"))
    assert artifacts[0]["if"] == "always()"
    assert artifacts[0]["with"]["retention-days"] == ARTIFACT_RETENTION_DAYS


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
