"""Structure of the admission policy `deploy/policies/kyverno-verify-images.yaml`.

Static and offline. The policy's value is the exact identity it trusts, so each constraint is
pinned here: a loosened regexp, a dropped repository claim or a switch to Audit fails the test.

Whether a real image is admitted (a signed one, an unsigned one) cannot be decided without a
registry. That is the live test on a kind cluster; `kyverno test` only covers the image-reference
match (see deploy/policies/tests/kyverno-test.yaml).
"""

from __future__ import annotations

from typing import Any, cast

import yaml

from tests.supply_chain._workflows import REPO, workflow

POLICY = REPO / "deploy" / "policies" / "kyverno-verify-images.yaml"
SUBJECT_RE = (
    r"^https://github\.com/<owner>/your-service-name/\.github/workflows/reusable-build\.yml"
    r"@refs/(heads/main|tags/v[^/]+)$"
)


def spec() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(POLICY.read_text(encoding="utf-8"))["spec"])


def keyless() -> dict[str, Any]:
    return cast("dict[str, Any]", spec()["attestors"][0]["cosign"]["keyless"])


def test_kind_and_enforcement() -> None:
    doc = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    assert doc["apiVersion"] == "policies.kyverno.io/v1"
    assert doc["kind"] == "ImageValidatingPolicy"
    assert spec()["validationActions"] == ["Deny"]
    assert spec()["failurePolicy"] == "Fail"


def test_matches_only_this_repositorys_images() -> None:
    assert spec()["matchImageReferences"] == [{"glob": "ghcr.io/<owner>/your-service-name/*"}]


def test_digest_and_required_settings() -> None:
    assert spec()["validationConfigurations"] == {
        "mutateDigest": True,
        "verifyDigest": True,
        "required": True,
    }


def test_identity_issuer_and_repository_claim() -> None:
    assert keyless()["identities"] == [
        {
            "issuer": "https://token.actions.githubusercontent.com",
            "subjectRegExp": SUBJECT_RE,
        }
    ]
    assert keyless()["additionalExtensions"] == {
        "githubWorkflowRepository": "<owner>/your-service-name"
    }
    assert spec()["attestors"][0]["cosign"]["ctlog"] == {"url": "https://rekor.sigstore.dev"}


def test_identity_is_the_reusable_build_workflow_that_signs() -> None:
    # The SAN names the workflow that runs `cosign sign`, so the regexp must name that file.
    assert "reusable-build" in SUBJECT_RE
    assert "sign-attest" in workflow("reusable-build.yml")["jobs"]


def test_requires_slsa_provenance_signed_by_the_same_attestor() -> None:
    assert spec()["attestations"] == [
        {"name": "provenance", "intoto": {"type": "https://slsa.dev/provenance/v1"}}
    ]
    exprs = [v["expression"] for v in spec()["validations"]]
    assert any("verifyImageSignatures" in e and "attestors.github" in e for e in exprs)
    assert any(
        "verifyAttestationSignatures" in e
        and "attestations.provenance" in e
        and "attestors.github" in e
        for e in exprs
    )


def test_kyverno_test_runs_in_the_blocking_repo_job() -> None:
    steps = workflow("ci.yml")["jobs"]["repo"]["steps"]
    assert any("kyverno" in s.get("run", "") and "install" in s.get("run", "") for s in steps)
    assert any(s.get("run") == "kyverno test deploy/policies/tests --require-tests" for s in steps)
