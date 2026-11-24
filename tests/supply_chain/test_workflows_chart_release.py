"""The signed OCI chart (reusable-chart-release.yml, called by release.yml). Offline."""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from typing import TYPE_CHECKING, Any, cast

import pytest
import yaml

from tests.backend.test_chart_production_guard import find_helm, require_helm
from tests.supply_chain._workflows import ARTIFACT_RETENTION_DAYS, REPO, workflow

if TYPE_CHECKING:
    from pathlib import Path

BASH: str = shutil.which("bash") or "/usr/bin/bash"
DIGEST = "sha256:" + "a" * 64
IDENTITY = (
    '--certificate-identity "https://github.com/${GITHUB_REPOSITORY}'
    '/.github/workflows/reusable-chart-release.yml@${GITHUB_REF}"'
)
ATTEST_GUARD = (
    "${{ github.event.repository.visibility == 'public' || vars.ATTESTATIONS_ENABLED == 'true' }}"
)


def _job() -> dict[str, Any]:
    return cast("dict[str, Any]", workflow("reusable-chart-release.yml")["jobs"]["chart"])


def _step(name: str) -> dict[str, Any]:
    return next(s for s in _job()["steps"] if s.get("name") == name)


def _index(needle: str) -> int:
    return next(
        i
        for i, s in enumerate(_job()["steps"])
        if needle in s.get("run", "") or str(s.get("uses", "")).startswith(needle)
    )


GRANTS = {"contents": "read", "packages": "write", "id-token": "write", "attestations": "write"}


def test_the_chart_is_published_only_on_a_pushed_v_tag_after_the_images_are_signed() -> None:
    doc = workflow("reusable-chart-release.yml")
    assert set(doc["on"]) == {"workflow_call"}
    assert doc["permissions"] == {}
    assert list(doc["jobs"]) == ["chart"]
    job = _job()
    assert job["if"] == "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
    assert "pull_request" not in str(job)
    assert "refs/heads/main" not in job["if"]
    assert job["permissions"] == GRANTS
    # release.yml calls it after the images job (every image signed), on a v* tag, with its grants.
    caller = workflow("release.yml")["jobs"]["chart"]
    assert caller["uses"] == "./.github/workflows/reusable-chart-release.yml"
    assert caller["needs"] == "images"
    assert caller["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert caller["permissions"] == GRANTS
    assert "chart" not in workflow("reusable-build.yml")["jobs"]


def test_only_the_release_assets_wait_for_the_chart() -> None:
    """A failed chart push holds back the draft release's assets, never the digests, the deploys or
    the VEX: the images' path to production does not depend on the chart."""
    jobs = workflow("release.yml")["jobs"]
    waiting = {
        n
        for n, j in jobs.items()
        if "chart" in ([j["needs"]] if isinstance(j.get("needs"), str) else j.get("needs", []))
    }
    assert waiting == {"release-assets"}
    assert "needs.chart.result == 'success'" in jobs["release-assets"]["if"]


def test_package_push_sign_verify_attest_in_order() -> None:
    order = [
        _index("scripts/tools.sh install helm4 cosign"),
        _index("helm package"),
        _index("helm push"),
        _index("cosign sign --yes"),
        _index("cosign verify"),
        _index("actions/attest-build-provenance@"),
        _index("actions/upload-artifact@"),
    ]
    assert order == sorted(order)
    assert _index("docker/login-action@") < _index("cosign sign --yes")


def test_signing_and_attestation_use_the_images_identity_rules() -> None:
    verify = _step("Verify the signature")["run"]
    for needle in (
        IDENTITY,
        "--certificate-oidc-issuer https://token.actions.githubusercontent.com",
        '--certificate-github-workflow-repository "${GITHUB_REPOSITORY}"',
        '--certificate-github-workflow-sha "${GITHUB_SHA}"',
    ):
        assert needle in verify
    assert "--certificate-identity-regexp" not in verify
    sign = _step("Sign the chart digest (keyless)")["run"]
    assert sign == 'cosign sign --yes "${REPOSITORY}@${DIGEST}"'  # by digest, never by tag
    attest = _step("Attest the chart's build provenance")
    assert attest["if"] == ATTEST_GUARD
    assert attest["with"] == {
        "subject-name": "${{ steps.package.outputs.repository }}",
        "subject-digest": "${{ steps.push.outputs.digest }}",
        "push-to-registry": True,
    }


def test_no_expression_is_inlined_in_a_run_and_the_reference_is_uploaded() -> None:
    for step in _job()["steps"]:
        assert "${{" not in step.get("run", ""), step.get("name")
    (upload,) = [s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/upload-")]
    assert upload["with"] == {
        "name": "chart",
        "path": "chart/chart.env",
        "retention-days": ARTIFACT_RETENTION_DAYS,
    }


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def _package(tmp_path: Path, helm: str, tag: str) -> tuple[dict[str, str], dict[str, Any]]:
    """Run the package step for real on the repository's chart; return outputs and Chart.yaml."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "helm").symlink_to(helm)
    out = tmp_path / "out"
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "GITHUB_REF_NAME": tag,
        "GITHUB_REPOSITORY": "Some-Owner/Some-Repo",
        "CHART_DIR": "charts/your-service-name",
        "REGISTRY": "ghcr.io",
    }
    run = _step("Package the chart at the tag's version")["run"]
    subprocess.run([BASH, "-e", "-c", run], cwd=REPO, env=env, check=True, capture_output=True)
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    with tarfile.open(outputs["package"]) as tgz:
        member = next(m for m in tgz.getmembers() if m.name.endswith("/Chart.yaml"))
        chart = yaml.safe_load(cast("Any", tgz.extractfile(member)).read())
    return outputs, chart


@pytest.mark.parametrize(("tag", "version"), [("v1.2.3", "1.2.3"), ("v2.0.0-rc.1", "2.0.0-rc.1")])
def test_the_packaged_chart_carries_the_tags_version(
    tmp_path: Path, helm: str, tag: str, version: str
) -> None:
    committed = yaml.safe_load((REPO / "charts/your-service-name/Chart.yaml").read_text())
    outputs, chart = _package(tmp_path, helm, tag)
    assert chart["version"] == version
    assert chart["appVersion"] == version
    assert committed["version"] != version  # the committed literal is not what ships
    assert outputs["version"] == version
    # Under the repository, as the images are (ghcr.io/<owner>/<repo>/<id>), lower-cased.
    assert outputs["repository"] == f"ghcr.io/some-owner/some-repo/charts/{committed['name']}"


PUSH_STUB = """#!/usr/bin/env bash
echo "helm $* config=${HELM_REGISTRY_CONFIG}" >> "$CALLS"
if [[ -n "${PUSH_FAILS:-}" ]]; then
  echo "Error: denied: permission_denied: write_package" >&2
  exit 1
fi
echo "Pushed: ghcr.io/o/r/charts/c:1.2.3"
echo "Digest: ${PUSHED_DIGEST}"
"""


def _push(tmp_path: Path, digest: str, *, fails: bool = False) -> tuple[int, str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "helm").write_text(PUSH_STUB)
    (bin_dir / "helm").chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CALLS": str(tmp_path / "calls"),
        "PUSHED_DIGEST": digest,
        "PUSH_FAILS": "1" if fails else "",
        "PACKAGE": "c-1.2.3.tgz",
        "HOME": str(tmp_path / "home"),
        "REGISTRY": "ghcr.io",
        "GITHUB_REPOSITORY": "O/R",
        "GITHUB_OUTPUT": str(tmp_path / "out"),
    }
    done = subprocess.run(
        [BASH, "-e", "-c", _step("Push the chart")["run"]],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    out = tmp_path / "out"
    return done.returncode, out.read_text() if out.exists() else "", done.stdout


def test_the_push_goes_to_the_repositorys_charts_namespace_and_outputs_the_digest(
    tmp_path: Path,
) -> None:
    code, outputs, _ = _push(tmp_path, DIGEST)
    assert code == 0
    assert outputs == f"digest={DIGEST}\n"
    calls = (tmp_path / "calls").read_text()
    # The docker/login-action login, so no token reaches a run step.
    config = tmp_path / "home" / ".docker" / "config.json"
    assert calls == f"helm push c-1.2.3.tgz oci://ghcr.io/o/r/charts config={config}\n"


def test_a_push_without_a_valid_digest_fails(tmp_path: Path) -> None:
    code, outputs, stdout = _push(tmp_path, "sha256:short")
    assert code != 0
    assert "::error::" in stdout
    assert outputs == ""


def test_a_failing_push_prints_helms_error_before_failing(tmp_path: Path) -> None:
    code, outputs, stdout = _push(tmp_path, DIGEST, fails=True)
    assert code != 0
    assert "denied: permission_denied: write_package" in stdout
    assert "::error::helm push failed" in stdout
    assert "re-run the failed jobs (wiki/releasing.md, The chart)" in stdout  # the recovery
    assert outputs == ""
