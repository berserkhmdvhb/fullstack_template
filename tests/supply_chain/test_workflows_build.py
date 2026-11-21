"""reusable-build.yml: build, SBOM, gate, keyless signing and attestations. Static, offline."""

from __future__ import annotations

import re
from typing import Any, cast

from tests.supply_chain._workflows import (
    ARTIFACT_RETENTION_DAYS,
    WORKFLOWS,
    steps_of,
    unpinned_uses,
    workflow,
)

IDENTITY = (
    '--certificate-identity "https://github.com/${GITHUB_REPOSITORY}'
    '/.github/workflows/reusable-build.yml@${GITHUB_REF}"'
)
ISSUER = "--certificate-oidc-issuer https://token.actions.githubusercontent.com"
WORKFLOW_SHA = '--certificate-github-workflow-sha "${GITHUB_SHA}"'
ATTESTATIONS = ("actions/attest-build-provenance@", "actions/attest-sbom@")


def _wf() -> dict[str, Any]:
    return workflow("reusable-build.yml")


def _jobs() -> dict[str, Any]:
    return cast("dict[str, Any]", _wf()["jobs"])


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def _uses(job: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    return [s for s in job["steps"] if str(s.get("uses", "")).startswith(prefix)]


def _index(job: dict[str, Any], needle: str) -> int:
    return next(
        i
        for i, s in enumerate(job["steps"])
        if needle in s.get("run", "") or str(s.get("uses", "")).startswith(needle)
    )


def test_it_is_a_reusable_workflow_with_empty_top_permissions() -> None:
    assert set(_wf()["on"]) == {"workflow_call"}
    assert set(_wf()["on"]["workflow_call"]["inputs"]) == {"matrix", "image_tag", "immutable"}
    assert _wf()["permissions"] == {}


def test_job_order_is_build_sbom_gate_sign() -> None:
    jobs = _jobs()
    assert list(jobs) == ["build", "sbom", "grype", "sign-attest"]
    assert "needs" not in jobs["build"]
    assert jobs["sbom"]["needs"] == "build"
    assert jobs["grype"]["needs"] == "sbom"
    assert set(jobs["sign-attest"]["needs"]) == {"build", "sbom", "grype"}


def test_only_the_signing_job_holds_packages_write_with_id_token_write() -> None:
    both = {
        n
        for n, j in _jobs().items()
        if j["permissions"].get("packages") == "write"
        and j["permissions"].get("id-token") == "write"
    }
    assert both == {"sign-attest"}
    assert _jobs()["sign-attest"]["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert _jobs()["build"]["permissions"] == {"contents": "read", "packages": "write"}
    assert _jobs()["sbom"]["permissions"] == {"contents": "read", "packages": "read"}
    assert _jobs()["grype"]["permissions"] == {"contents": "read", "security-events": "write"}
    assert not [n for n in ("build", "sbom", "grype") if "id-token" in _jobs()[n]["permissions"]]


def test_every_job_has_a_timeout_and_every_use_is_pinned() -> None:
    for name, job in _jobs().items():
        assert isinstance(job["timeout-minutes"], int), name
    assert unpinned_uses(WORKFLOWS / "reusable-build.yml") == []


def test_the_scan_jobs_behave_as_before() -> None:
    jobs = _jobs()
    build = _uses(jobs["build"], "docker/build-push-action@")[0]["with"]
    assert build["push"] is True
    assert build["cache-from"] == "type=gha,scope=${{ matrix.id }}"
    assert build["cache-to"] == "type=gha,mode=max,scope=${{ matrix.id }}"
    assert "infra/security/sbom-generate.sh" in _runs(jobs["sbom"])
    assert "infra/security/sbom-generate.sh" not in _runs(jobs["build"])
    assert not _uses(jobs["grype"], "docker/login-action@")
    gate = [s for s in jobs["grype"]["steps"] if "scan-vulnerabilities.sh" in s.get("run", "")]
    assert len(gate) == 1
    assert "continue-on-error" not in gate[0]
    assert "continue-on-error" not in jobs["grype"]
    assert not re.search(r"fail[-_]level|FAIL_LEVEL|--fail-on|\s-l\s", _runs(jobs["grype"]))
    (sarif,) = _uses(jobs["grype"], "github/codeql-action/upload-sarif@")
    assert sarif["if"] == "always()"
    assert sarif["continue-on-error"] is True


def test_digest_is_checked_against_the_registry_after_the_push() -> None:
    steps = _jobs()["build"]["steps"]
    push = next(
        i
        for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith("docker/build-push-action@")
    )
    verify = next(i for i, s in enumerate(steps) if "imagetools inspect" in s.get("run", ""))
    assert verify > push
    assert '"${resolved}" != "${BUILD_DIGEST}"' in steps[verify]["run"]
    assert "steps." not in steps[verify]["run"]


def test_latest_only_on_immutable_releases() -> None:
    job = _jobs()["build"]
    assert job["env"]["IMAGE_REF"] == "${{ matrix.repository }}:${{ inputs.image_tag }}"
    tags = [
        t.strip()
        for t in _uses(job, "docker/build-push-action@")[0]["with"]["tags"].split("\n")
        if t.strip()
    ]
    assert tags[0] == "${{ env.IMAGE_REF }}"
    assert tags[1].startswith("${{ inputs.immutable == 'true' && ")
    assert len(tags) == 2  # noqa: PLR2004 — the versioned tag and the conditional latest


def test_every_upload_keeps_90_days() -> None:
    uploads = [s for j in _jobs().values() for s in _uses(j, "actions/upload-artifact@")]
    assert uploads
    assert all(s["with"]["retention-days"] == ARTIFACT_RETENTION_DAYS for s in uploads)


def test_cosign_comes_from_the_pinned_tools_script_not_an_action() -> None:
    job = _jobs()["sign-attest"]
    assert "scripts/tools.sh install cosign" in _runs(job)
    assert not [s for s in steps_of(_wf()) if "cosign-installer" in str(s.get("uses", ""))]
    assert "docker/login-action@" in str([s.get("uses") for s in job["steps"]])
    assert not re.search(r"secrets\.|docker login", _runs(job))


def test_verify_runs_after_sign_and_both_attestations_after_verify() -> None:
    job = _jobs()["sign-attest"]
    sign = _index(job, "cosign sign")
    verify = _index(job, "cosign verify")
    provenance = _index(job, "actions/attest-build-provenance@")
    sbom = _index(job, "actions/attest-sbom@")
    assert _index(job, "scripts/tools.sh install cosign") < sign < verify < min(provenance, sbom)
    assert _index(job, "docker/login-action@") < sign


def test_verify_pins_the_workflow_identity_issuer_and_commit() -> None:
    job = _jobs()["sign-attest"]
    verify = job["steps"][_index(job, "cosign verify")]["run"]
    assert IDENTITY in verify
    assert ISSUER in verify
    assert WORKFLOW_SHA in verify
    assert "--certificate-identity-regexp" not in verify
    sign = job["steps"][_index(job, "cosign sign")]["run"]
    assert "cosign sign --yes " in sign
    assert "${{" not in sign + verify  # values reach the shell through env, never inline


def test_attestations_push_to_the_registry_by_digest() -> None:
    job = _jobs()["sign-attest"]
    for prefix in ATTESTATIONS:
        (step,) = _uses(job, prefix)
        assert step["with"]["push-to-registry"] is True
        assert step["with"]["subject-name"] == "${{ matrix.repository }}"
        assert step["with"]["subject-digest"] == "${{ steps.ref.outputs.digest }}"
    (sbom,) = _uses(job, "actions/attest-sbom@")
    assert sbom["with"]["sbom-path"].endswith("/sbom/${{ matrix.id }}.image.sbom.cdx.json")


def test_signing_reads_the_digest_the_build_verified() -> None:
    job = _jobs()["sign-attest"]
    ref = next(s for s in job["steps"] if s.get("id") == "ref")
    assert "digest.env" in ref["run"]
    assert "sha256:[0-9a-f]{64}" in ref["run"]
    names = [s["with"]["name"] for s in _uses(job, "actions/download-artifact@")]
    assert names == ["digest-${{ matrix.id }}", "sbom-${{ matrix.id }}"]
