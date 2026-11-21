"""Structure of the admission policies in `deploy/policies/`.

Static and offline. The policies' value is the exact identity they trust and the exact deny logic,
so the WHOLE `spec` of each is compared with the expected one: any change to a policy must change
this test deliberately. The readable assertions below the whole-spec ones name the invariants.

Whether a real image is admitted (a signed one, an unsigned one) cannot be decided without a
registry. That is the live test on a kind cluster; `kyverno test` only covers the image-reference
match and, for the alias policy, the full CEL (see deploy/policies/tests/).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

from tests.supply_chain._workflows import REPO, workflow

POLICIES = REPO / "deploy" / "policies"
VERIFY = POLICIES / "kyverno-verify-images.yaml"
ALIASES = POLICIES / "kyverno-deny-image-aliases.yaml"
SUBJECT_RE = (
    r"^https://github\.com/<Owner>/<Repo>/\.github/workflows/reusable-build\.yml"
    r"@refs/(heads/main|tags/v[^/]+)$"
)
RULES = [
    {
        "apiGroups": [""],
        "apiVersions": ["v1"],
        "operations": ["CREATE", "UPDATE"],
        "resources": ["pods", "pods/ephemeralcontainers"],
    }
]

EXPECTED_VERIFY_SPEC: dict[str, Any] = {
    "validationActions": ["Deny"],
    "failurePolicy": "Fail",
    "webhookConfiguration": {"timeoutSeconds": 30},
    "matchConstraints": {"resourceRules": RULES},
    "matchImageReferences": [{"glob": "ghcr.io/<owner>/<repo>/*"}],
    "validationConfigurations": {"mutateDigest": True, "verifyDigest": True, "required": True},
    "attestors": [
        {
            "name": "github",
            "cosign": {
                "keyless": {
                    "identities": [
                        {
                            "issuer": "https://token.actions.githubusercontent.com",
                            "subjectRegExp": SUBJECT_RE,
                        }
                    ],
                    "additionalExtensions": {"githubWorkflowRepository": "<Owner>/<Repo>"},
                },
                "ctlog": {"url": "https://rekor.sigstore.dev"},
            },
        }
    ],
    "attestations": [{"name": "provenance", "intoto": {"type": "https://slsa.dev/provenance/v1"}}],
    "variables": [
        {
            "name": "allImages",
            "expression": (
                "images.?containers.orValue([]) + images.?initContainers.orValue([])"
                " + images.?ephemeralContainers.orValue([])"
            ),
        }
    ],
    "validations": [
        {
            "expression": (
                "variables.allImages.map(image, verifyImageSignatures(image,"
                " [attestors.github])).all(count, count > 0)"
            ),
            "message": (
                "Images must be signed by this repository's build workflow"
                " (see wiki/admission-control.md)."
            ),
        },
        {
            "expression": (
                "variables.allImages.map(image, verifyAttestationSignatures(image,"
                " attestations.provenance, [attestors.github])).all(count, count > 0)"
            ),
            "message": (
                "Images must carry a SLSA build provenance attestation from this"
                " repository's build workflow."
            ),
        },
    ],
}

EXPECTED_ALIAS_SPEC: dict[str, Any] = {
    "validationActions": ["Deny"],
    "failurePolicy": "Fail",
    "matchConstraints": {"resourceRules": RULES},
    "variables": [
        {
            "name": "images",
            "expression": (
                "object.spec.containers.map(c, c.image) + "
                "object.spec.?initContainers.orValue([]).map(c, c.image) + "
                "object.spec.?ephemeralContainers.orValue([]).map(c, c.image)"
            ),
        }
    ],
    "validations": [
        {
            "expression": (
                "variables.images.all(i, i.startsWith('ghcr.io/<owner>/<repo>/') || "
                "!i.lowerAscii().replace('ghcr.io:443/', 'ghcr.io/')"
                ".startsWith('ghcr.io/<owner>/<repo>/'))"
            ),
            "message": (
                "Use the canonical lowercase image name ghcr.io/<owner>/<repo>/...; a port or a"
                " different case would skip signature verification."
            ),
        }
    ],
}


def load(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))


def spec() -> dict[str, Any]:
    return cast("dict[str, Any]", load(VERIFY)["spec"])


def test_whole_spec_of_the_signature_policy() -> None:
    assert spec() == EXPECTED_VERIFY_SPEC
    assert len(spec()["attestors"]) == 1  # a second, looser attestor would widen the trust


def test_whole_spec_of_the_alias_policy() -> None:
    assert load(ALIASES)["spec"] == EXPECTED_ALIAS_SPEC


def test_kinds_and_api_versions() -> None:
    assert (load(VERIFY)["apiVersion"], load(VERIFY)["kind"]) == (
        "policies.kyverno.io/v1",
        "ImageValidatingPolicy",
    )
    assert (load(ALIASES)["apiVersion"], load(ALIASES)["kind"]) == (
        "policies.kyverno.io/v1",
        "ValidatingPolicy",
    )


def test_both_policies_deny_and_fail_closed() -> None:
    for p in (VERIFY, ALIASES):
        assert load(p)["spec"]["validationActions"] == ["Deny"]
        assert load(p)["spec"]["failurePolicy"] == "Fail"


def test_ephemeral_containers_are_evaluated() -> None:
    for p in (VERIFY, ALIASES):
        res = load(p)["spec"]["matchConstraints"]["resourceRules"][0]["resources"]
        assert "pods/ephemeralcontainers" in res


def test_identity_is_the_reusable_build_workflow_that_signs() -> None:
    # The SAN names the workflow that runs `cosign sign`, so the regexp must name that file.
    assert "reusable-build" in SUBJECT_RE
    assert "sign-attest" in workflow("reusable-build.yml")["jobs"]


def test_placeholders_use_the_two_spellings() -> None:
    text = VERIFY.read_text(encoding="utf-8") + ALIASES.read_text(encoding="utf-8")
    assert not re.search(r"your[-_]service[-_]name", text, re.IGNORECASE)
    assert "<owner>/<repo>" in text
    assert "<Owner>/<Repo>" in text


def test_kyverno_test_runs_in_the_blocking_repo_job() -> None:
    steps = workflow("ci.yml")["jobs"]["repo"]["steps"]
    assert any("kyverno" in s.get("run", "") and "install" in s.get("run", "") for s in steps)
    assert any(s.get("run") == "kyverno test deploy/policies --require-tests" for s in steps)


def test_policy_changes_trigger_the_tests_on_pull_requests() -> None:
    # The structure tests run in the backend job; its path filter must see these files.
    run = next(
        s["run"] for s in workflow("ci.yml")["jobs"]["changes"]["steps"] if s.get("id") == "filter"
    )
    backend = next(line for line in run.splitlines() if "grep -Eq" in line and "src/" in line)
    assert "deploy/" in backend
    assert "wiki/" in backend


def test_repo_job_tool_cache_key_names_the_tool_list() -> None:
    steps = workflow("ci.yml")["jobs"]["repo"]["steps"]
    install = next(s["run"] for s in steps if s.get("name") == "Install the pinned tools")
    tools = re.search(r"tools\.sh install (.+)", install)
    assert tools
    keys = [s["with"]["key"] for s in steps if str(s.get("uses", "")).startswith("actions/cache")]
    assert len(keys) == len("rs")  # restore and save
    for key in keys:
        assert "-".join(tools.group(1).split()) in key
