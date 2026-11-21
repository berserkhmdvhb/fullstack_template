"""scripts/render-admission-policy.sh fills the admission policies' placeholders.

The image glob uses the lowercase registry path, the certificate subject and the repository claim
use GitHub's exact case, and the subject is a regexp, so dots are escaped. The adapter must not
touch these files: they hold no service-name placeholder.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, cast

import pytest
import yaml

from tests.supply_chain._workflows import REPO

BASH = shutil.which("bash") or "/bin/bash"
SCRIPT = REPO / "scripts" / "render-admission-policy.sh"
EXPECTED = ("verify", "aliases")
POLICIES = sorted((REPO / "deploy" / "policies").glob("*.yaml"))


def render(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, check=False)


def docs(out: str) -> list[dict[str, Any]]:
    return [cast("dict[str, Any]", d) for d in yaml.safe_load_all(out) if d]


def test_policies_exist_and_hold_no_service_name_placeholder() -> None:
    assert len(POLICIES) == len(EXPECTED)
    for p in POLICIES:
        text = p.read_text(encoding="utf-8").lower()
        assert "your-service-name" not in text
        assert "your_service_name" not in text
        assert "<owner>/<repo>" in text


def test_renders_mixed_case_and_dots() -> None:
    r = render("Acme-Corp/Pay.ments_svc")
    assert r.returncode == 0, r.stderr
    verify = next(d for d in docs(r.stdout) if d["kind"] == "ImageValidatingPolicy")
    keyless = verify["spec"]["attestors"][0]["cosign"]["keyless"]
    assert verify["spec"]["matchImageReferences"] == [{"glob": "ghcr.io/acme-corp/pay.ments_svc/*"}]
    assert keyless["additionalExtensions"] == {
        "githubWorkflowRepository": "Acme-Corp/Pay.ments_svc"
    }
    assert keyless["identities"][0]["subjectRegExp"] == (
        r"^https://github\.com/Acme-Corp/Pay\.ments_svc/\.github/workflows/reusable-build\.yml"
        r"@refs/(heads/main|tags/v[^/]+)$"
    )
    alias = next(d for d in docs(r.stdout) if d["kind"] == "ValidatingPolicy")
    assert "ghcr.io/acme-corp/pay.ments_svc/" in yaml.safe_dump(alias)
    assert "<" not in r.stdout.replace("<=", "")


@pytest.mark.parametrize(
    "bad", ["", "noslash", "a/b/c", "a b/c", "o/", "/r", "o/r;rm", "o/$(x)", "o/r&x", "o|x/r"]
)
def test_rejects_bad_input(bad: str) -> None:
    r = render(bad) if bad else render()
    assert r.returncode != 0
    assert r.stdout == ""


def test_rejects_extra_arguments() -> None:
    assert render("a/b", "c/d").returncode != 0
