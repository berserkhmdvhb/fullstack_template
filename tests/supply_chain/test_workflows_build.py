"""reusable-build.yml: build, SBOM, gate, keyless signing and attestations. Static, offline."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Any, cast

import pytest
import yaml

if TYPE_CHECKING:
    from pathlib import Path

from tests.supply_chain._workflows import (
    ARTIFACT_RETENTION_DAYS,
    REPO,
    WORKFLOWS,
    plan_for,
    steps_of,
    unpinned_uses,
    workflow,
)

BASH: str = shutil.which("bash") or "/usr/bin/bash"
IDENTITY = (
    '--certificate-identity "https://github.com/${GITHUB_REPOSITORY}'
    '/.github/workflows/reusable-build.yml@${GITHUB_REF}"'
)
ISSUER = "--certificate-oidc-issuer https://token.actions.githubusercontent.com"
REPOSITORY_CLAIM = '--certificate-github-workflow-repository "${GITHUB_REPOSITORY}"'
ATTEST_GUARD = (
    "github.event.repository.visibility == 'public' || vars.ATTESTATIONS_ENABLED == 'true'"
)
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


def _expr(step: dict[str, Any]) -> str:
    """The step's `if:` without the `${{ }}` wrapper."""
    return re.sub(r"^\$\{\{\s*(.*?)\s*\}\}$", r"\1", str(step["if"]).strip())


def _index(job: dict[str, Any], needle: str) -> int:
    return next(
        i
        for i, s in enumerate(job["steps"])
        if needle in s.get("run", "") or str(s.get("uses", "")).startswith(needle)
    )


def test_it_is_a_reusable_workflow_with_empty_top_permissions() -> None:
    assert set(_wf()["on"]) == {"workflow_call"}
    inputs = set(_wf()["on"]["workflow_call"]["inputs"])
    assert inputs == {"matrix", "platform_matrix", "image_tag", "immutable", "prerelease"}
    assert _wf()["permissions"] == {}


def test_job_order_is_build_merge_sbom_gate_sign() -> None:
    jobs = _jobs()
    assert list(jobs) == [
        "build",
        "merge",
        "sbom",
        "grype",
        "image-checks",
        "sign-attest",
        "attest-sbom",
    ]
    assert "needs" not in jobs["build"]
    assert jobs["merge"]["needs"] == "build"
    assert jobs["sbom"]["needs"] == "build"
    assert jobs["grype"]["needs"] == "sbom"
    assert jobs["image-checks"]["needs"] == "build"
    assert set(jobs["sign-attest"]["needs"]) == {"build", "merge", "sbom", "grype", "image-checks"}
    # The SBOM attestation waits for exactly what signing waits for, including the merge: no
    # attestation lands on platform manifests that no signed index points to.
    assert set(jobs["attest-sbom"]["needs"]) == set(jobs["sign-attest"]["needs"])
    # The chart is reusable-chart-release.yml's, after release.yml's images job (no chart for images
    # that failed the gate; tests/supply_chain/test_workflows_chart_release.py).


def test_per_platform_jobs_use_the_platform_matrix_and_per_image_jobs_the_image_matrix() -> None:
    jobs = _jobs()
    for name in ("build", "sbom", "grype", "image-checks", "attest-sbom"):
        assert jobs[name]["strategy"]["matrix"] == "${{ fromJSON(inputs.platform_matrix) }}", name
    for name in ("merge", "sign-attest"):
        assert jobs[name]["strategy"]["matrix"] == "${{ fromJSON(inputs.matrix) }}", name


def test_builds_and_checks_run_on_the_native_runner_of_their_platform() -> None:
    jobs = _jobs()
    for name in ("build", "image-checks"):
        assert jobs[name]["runs-on"] == "${{ matrix.runner }}", name
    for name in ("merge", "sbom", "grype", "sign-attest", "attest-sbom"):
        assert "matrix." not in str(jobs[name]["runs-on"]), name


def test_no_emulation_anywhere_in_the_github_directory() -> None:
    for path in sorted((REPO / ".github").rglob("*")):
        if path.is_file() and path.suffix in {".yml", ".yaml", ".py", ".sh", ".md", ".json"}:
            text = path.read_text(encoding="utf-8").lower()
            for word in ("qemu", "binfmt"):
                assert word not in text, f"{word} in {path.relative_to(REPO)}"


def _runner_map() -> dict[str, str]:
    path = REPO / ".github" / "container-images.yml"
    return cast("dict[str, str]", yaml.safe_load(path.read_text(encoding="utf-8"))["runners"])


def test_the_manifest_maps_each_platform_to_its_own_architectures_runner() -> None:
    runners = _runner_map()
    assert set(runners) >= {"linux/amd64", "linux/arm64"}
    for platform, runner in runners.items():
        arch = platform.split("/")[1]
        assert (arch == "arm64") == runner.endswith("-arm"), (platform, runner)


def test_no_workflow_hard_codes_a_runner_for_a_platform() -> None:
    for path in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"ubuntu-[\d.]+-arm|\barm64-runner|runs-on:.*arm", text), path.name


def test_each_build_step_builds_one_platform_taken_from_the_matrix() -> None:
    (step,) = _uses(_jobs()["build"], "docker/build-push-action@")
    assert step["with"]["platforms"] == "${{ matrix.platform }}"
    assert "," not in step["with"]["platforms"]


def test_build_pushes_by_digest_only_without_a_tag() -> None:
    (step,) = _uses(_jobs()["build"], "docker/build-push-action@")
    outputs = step["with"]["outputs"]
    assert outputs == (
        "type=image,name=${{ matrix.repository }},push-by-digest=true,name-canonical=true,push=true"
    )
    assert "tags" not in step["with"]
    assert "push" not in step["with"]
    assert step["with"]["provenance"] is False
    assert step["with"]["sbom"] is False


CACHE_WORDS = ("cache-from", "cache-to", "type=gha", "type=registry", "actions/cache")


def _without_comments(name: str) -> str:
    """The workflow as parsed data (comments are not data), for substring checks."""
    return json.dumps(workflow(name))


def test_comments_do_not_count_as_cache_use(tmp_path: Path) -> None:
    path = tmp_path / "w.yml"
    path.write_text("# actions/cache is not used here\non: push\njobs: {}\n", encoding="utf-8")
    assert "actions/cache" not in json.dumps(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert "actions/cache" in path.read_text(encoding="utf-8")


def test_release_builds_use_no_cache() -> None:
    (step,) = _uses(_jobs()["build"], "docker/build-push-action@")
    assert not {"cache-from", "cache-to"} & set(step["with"])
    for name in ("reusable-build.yml", "release.yml"):
        text = _without_comments(name)
        for word in CACHE_WORDS:
            assert word not in text, (name, word)
        for job_step in steps_of(workflow(name)):
            assert "cache" not in (job_step.get("with") or {}), (name, job_step)
            assert not str(job_step.get("uses", "")).startswith("./.github/actions/setup-"), name


def test_setup_buildx_does_not_cache_its_binary() -> None:
    steps = [s for j in _jobs().values() for s in _uses(j, "docker/setup-buildx-action@")]
    assert len(steps) == 2  # noqa: PLR2004 — build and merge
    for step in steps:
        assert step["with"]["cache-binary"] is False  # the action's default is true


def test_the_merge_job_builds_the_index_from_the_per_platform_digests() -> None:
    job = _jobs()["merge"]
    runs = _runs(job)
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert "docker buildx imagetools create" in runs
    assert "${{" not in runs
    (down,) = _uses(job, "actions/download-artifact@")
    assert down["with"]["pattern"] == "platform-${{ matrix.id }}.*"
    assert down["with"]["merge-multiple"] is True
    (up,) = _uses(job, "actions/upload-artifact@")
    assert up["with"]["name"] == "digest-${{ matrix.id }}"
    create = _index(job, "docker buildx imagetools create")
    inspect = _index(job, "imagetools inspect")
    assert create < inspect


def test_the_merge_job_resolves_the_index_digest_and_fails_on_a_bad_one() -> None:
    step = next(s for s in _jobs()["merge"]["steps"] if "imagetools inspect" in s.get("run", ""))
    run = step["run"]
    assert run.lstrip().startswith("set -euo pipefail")
    assert "{{.Manifest.Digest}}" in run
    assert "sha256:[0-9a-f]{64}" in run
    assert "exit 1" in run


def test_only_the_signing_job_holds_packages_write_with_id_token_write() -> None:
    both = {
        n
        for n, j in _jobs().items()
        if j["permissions"].get("packages") == "write"
        and j["permissions"].get("id-token") == "write"
    }
    assert both == {"sign-attest", "attest-sbom"}
    for name in both:
        assert _jobs()[name]["permissions"] == {
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


def test_the_platform_digest_is_checked_against_the_registry_after_the_push() -> None:
    steps = _jobs()["build"]["steps"]
    push = next(
        i
        for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith("docker/build-push-action@")
    )
    verify = next(i for i, s in enumerate(steps) if "imagetools inspect" in s.get("run", ""))
    assert verify > push
    assert '"${resolved}" != "${BUILD_DIGEST}"' in steps[verify]["run"]
    assert '"${REPOSITORY}@${BUILD_DIGEST}"' in steps[verify]["run"]
    assert "steps." not in steps[verify]["run"]


def test_latest_only_on_immutable_releases_and_only_on_the_index() -> None:
    job = _jobs()["merge"]
    assert job["env"]["IMAGE_REF"] == "${{ matrix.repository }}:${{ inputs.image_tag }}"
    assert job["env"]["IMMUTABLE"] == "${{ inputs.immutable }}"
    assert job["env"]["PRERELEASE"] == "${{ inputs.prerelease }}"
    run = _runs(job)
    assert run.count(":latest") == 1
    assert not [n for n, j in _jobs().items() if n != "merge" and ":latest" in _runs(j)]


def _tags_for(
    tag: str,
    ref_type: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prerelease: str | None = None,
) -> list[str]:
    """The tags the merge step applies, for a ref planned by container_matrix.py (stub docker)."""
    plan = plan_for(tag, monkeypatch, ref_type)
    step = next(
        s for s in _jobs()["merge"]["steps"] if s.get("name") == "Create the index and tag it"
    )
    work = tmp_path / tag
    (work / "platform").mkdir(parents=True)
    (work / "platform" / "backend.linux-amd64.digest.env").write_text(
        f"BACKEND_IMAGE_DIGEST=sha256:{'b' * 64}\n"
    )
    bin_dir = work / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" > "$CALLS"\n')
    (bin_dir / "docker").chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CALLS": str(work / "calls"),
        "IMAGE_ID": "backend",
        "REPOSITORY": "ghcr.io/o/r/backend",
        "IMAGE_REF": f"ghcr.io/o/r/backend:{plan['image_tag']}",
        "IMMUTABLE": plan["immutable"],
        "PRERELEASE": plan["prerelease"] if prerelease is None else prerelease,
    }
    subprocess.run([BASH, "-e", "-c", step["run"]], cwd=work, env=env, check=True)
    args = (work / "calls").read_text().splitlines()
    return [args[i + 1] for i, a in enumerate(args) if a == "--tag"]


@pytest.mark.parametrize(
    ("ref", "ref_type", "latest"),
    [("v1.2.3", "tag", True), ("v1.2.3-rc.1", "tag", False), ("main", "branch", False)],
)
def test_latest_moves_only_for_a_final_release(
    ref: str, ref_type: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, latest: bool
) -> None:
    tags = _tags_for(ref, ref_type, tmp_path, monkeypatch)
    assert tags[0].startswith("ghcr.io/o/r/backend:")
    assert ("ghcr.io/o/r/backend:latest" in tags) is latest
    assert len(tags) == (2 if latest else 1)


def test_latest_fails_closed_without_the_prerelease_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert len(_tags_for("v1.2.3", "tag", tmp_path, monkeypatch, prerelease="")) == 1


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


def test_verify_runs_after_sign_and_the_provenance_after_verify() -> None:
    job = _jobs()["sign-attest"]
    sign = _index(job, "cosign sign")
    verify = _index(job, "cosign verify")
    provenance = _index(job, "actions/attest-build-provenance@")
    assert _index(job, "scripts/tools.sh install cosign") < sign < verify < provenance
    assert _index(job, "docker/login-action@") < sign
    assert not _uses(job, "actions/attest-sbom@")


def test_verify_pins_the_workflow_identity_issuer_and_commit() -> None:
    job = _jobs()["sign-attest"]
    verify = job["steps"][_index(job, "cosign verify")]["run"]
    assert IDENTITY in verify
    assert ISSUER in verify
    assert WORKFLOW_SHA in verify
    assert REPOSITORY_CLAIM in verify  # a foreign repo can call this workflow and get our SAN
    assert "--certificate-identity-regexp" not in verify
    sign = job["steps"][_index(job, "cosign sign")]["run"]
    assert "cosign sign --yes " in sign
    assert "${{" not in sign + verify  # values reach the shell through env, never inline


def test_attestations_push_to_the_registry_by_digest() -> None:
    (provenance,) = _uses(_jobs()["sign-attest"], "actions/attest-build-provenance@")
    assert provenance["with"]["subject-digest"] == "${{ steps.ref.outputs.digest }}"  # the index
    job = _jobs()["attest-sbom"]
    (sbom,) = _uses(job, "actions/attest-sbom@")
    for step in (provenance, sbom):
        assert step["with"]["push-to-registry"] is True
        assert step["with"]["subject-name"] == "${{ matrix.repository }}"
    # One SBOM per (image, platform), attested on that platform's manifest digest.
    assert sbom["with"]["subject-digest"] == "${{ steps.ref.outputs.digest }}"
    assert sbom["with"]["sbom-path"] == "sbom/${{ matrix.id }}.image.sbom.cdx.json"


def test_the_sbom_attestation_job_reads_its_own_platforms_artifacts() -> None:
    job = _jobs()["attest-sbom"]
    downloads = [s["with"]["name"] for s in _uses(job, "actions/download-artifact@")]
    assert downloads == [
        "platform-${{ matrix.id }}.${{ matrix.slug }}",
        "sbom-${{ matrix.id }}.${{ matrix.slug }}",
    ]
    ref = next(s for s in job["steps"] if s.get("id") == "ref")
    assert "${IMAGE_ID}.${SLUG}.digest.env" in ref["run"]
    assert job["env"]["SLUG"] == "${{ matrix.slug }}"


def test_the_workflow_names_no_platform_outside_matrix_expressions() -> None:
    text = (WORKFLOWS / "reusable-build.yml").read_text(encoding="utf-8")
    outside = re.sub(r"\$\{\{.*?\}\}", "", text, flags=re.DOTALL)
    for name in ("linux/amd64", "linux/arm64", "amd64", "arm64"):
        assert name not in outside, name


def test_the_sbom_attestation_job_covers_exactly_the_platforms_the_helper_renders() -> None:
    job = _jobs()["attest-sbom"]
    assert job["strategy"]["matrix"] == "${{ fromJSON(inputs.platform_matrix) }}"
    env = {
        **os.environ,
        "GITHUB_REPOSITORY_OWNER": "o",
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_REF_NAME": "main",
        "GITHUB_REF_TYPE": "branch",
        "GITHUB_SHA": "0123456789abcdef0123456789abcdef01234567",
        "BUILD_ARM64": "true",
        "GITHUB_OUTPUT": "",
    }
    env.pop("GITHUB_OUTPUT")
    out = subprocess.run(
        [sys.executable, str(REPO / ".github/scripts/container_matrix.py"), "--matrix"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    legs = json.loads(next(ln for ln in out.splitlines() if ln.startswith("platform_matrix="))[16:])
    manifest = yaml.safe_load((REPO / ".github/container-images.yml").read_text(encoding="utf-8"))
    wanted = {(i["id"], p) for i in manifest["images"] for p in i["platforms"]}
    assert {(e["id"], e["platform"]) for e in legs["include"]} == wanted
    assert len(legs["include"]) == len(wanted)  # one attestation leg per (image, platform)


def _run_ref_step(
    job: str, tmp_path: Path, env_file: str | None
) -> subprocess.CompletedProcess[str]:
    step = next(s for s in _jobs()[job]["steps"] if s.get("id") == "ref")
    (tmp_path / "parts").mkdir()
    if env_file is not None:
        (tmp_path / "parts" / "app.linux-amd64.digest.env").write_text(env_file)
        (tmp_path / "parts" / "app.digest.env").write_text(env_file)
    out = tmp_path / "out"
    return subprocess.run(
        [BASH, "-c", step["run"]],
        cwd=tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "IMAGE_ID": "app",
            "SLUG": "linux-amd64",
            "GITHUB_OUTPUT": str(out),
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("job", ["sign-attest", "attest-sbom"])
def test_a_bad_digest_fails_the_step_with_the_error_on_stderr(job: str, tmp_path: Path) -> None:
    for content in ("APP_IMAGE_DIGEST=sha256:short\n", "APP_IMAGE_DIGEST=\n", None):
        sub = tmp_path / str(abs(hash(content)))
        sub.mkdir()
        res = _run_ref_step(job, sub, content)
        assert res.returncode != 0, content
        assert "::error::" in res.stderr
        assert "::error::" not in res.stdout
        assert not (sub / "out").exists() or "::error" not in (sub / "out").read_text()


@pytest.mark.parametrize("job", ["sign-attest", "attest-sbom"])
def test_a_good_digest_is_written_to_the_output(job: str, tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    res = _run_ref_step(job, tmp_path, f"APP_IMAGE_DIGEST={digest}\n")
    assert res.returncode == 0, res.stderr
    assert (tmp_path / "out").read_text() == f"digest={digest}\n"


def test_signing_reads_the_digest_the_build_verified() -> None:
    job = _jobs()["sign-attest"]
    ref = next(s for s in job["steps"] if s.get("id") == "ref")
    assert "digest.env" in ref["run"]
    assert "sha256:[0-9a-f]{64}" in ref["run"]
    downloads = [s["with"]["name"] for s in _uses(job, "actions/download-artifact@")]
    assert downloads == ["digest-${{ matrix.id }}"]


# --- assertions carried over from the release workflow tests when the jobs moved here -------------


def test_one_buildx_one_login_one_build_push_and_only_the_platform_upload_in_build() -> None:
    job = _jobs()["build"]
    for prefix in (
        "docker/setup-buildx-action@",
        "docker/login-action@",
        "docker/build-push-action@",
    ):
        assert len(_uses(job, prefix)) == 1, prefix
    (up,) = _uses(job, "actions/upload-artifact@")
    assert up["with"]["name"] == "platform-${{ matrix.id }}.${{ matrix.slug }}"
    assert "fromJSON(inputs.platform_matrix)" in str(job["strategy"]["matrix"])


def test_login_token_goes_through_with_not_run() -> None:
    seen = 0
    for name, job in _jobs().items():
        for step in _uses(job, "docker/login-action@"):
            seen += 1
            assert step["with"]["registry"] == "ghcr.io"
            assert step["with"]["username"] == "${{ github.actor }}"
            assert step["with"]["password"] == "${{ secrets.GITHUB_TOKEN }}"  # noqa: S105 — a workflow expression, not a credential
        assert not re.search(r"secrets\.|GITHUB_TOKEN|docker login", _runs(job)), name
    assert seen == 6  # noqa: PLR2004 — build, merge, sbom, image-checks, sign-attest, attest-sbom


def test_digest_verify_stops_the_job_on_a_mismatch() -> None:
    step = next(s for s in _jobs()["build"]["steps"] if "imagetools inspect" in s.get("run", ""))
    run = step["run"]
    assert "exit 1" in run
    assert "docker buildx imagetools inspect" in run
    assert "{{.Manifest.Digest}}" in run
    assert step["env"]["BUILD_DIGEST"] == "${{ steps.build.outputs.digest }}"


def test_grype_installs_the_pinned_tool_and_names_no_severity() -> None:
    job = _jobs()["grype"]
    assert "infra/security/tools-install.sh grype" in _runs(job)
    assert "infra/security/scan-vulnerabilities.sh" in _runs(job)
    raw = str(job).lower().replace("security-events", "")
    assert not re.search(r"\b(high|critical)\b", raw)


def test_grype_sarif_failure_warns_and_the_report_artifact_is_always_kept() -> None:
    job = _jobs()["grype"]
    (sarif,) = _uses(job, "github/codeql-action/upload-sarif@")
    assert sarif["with"]["category"] == "grype-${{ matrix.id }}-${{ matrix.slug }}"
    warn = [s for s in job["steps"] if "steps.sarif.outcome == 'failure'" in str(s.get("if", ""))]
    assert len(warn) == 1
    assert "::warning" in warn[0]["run"]
    assert "GITHUB_STEP_SUMMARY" in warn[0]["run"]
    artifacts = _uses(job, "actions/upload-artifact@")
    assert artifacts[0]["if"] == "always()"
    assert artifacts[0]["with"]["retention-days"] == ARTIFACT_RETENTION_DAYS


def test_every_job_writes_a_step_summary() -> None:
    for name, job in _jobs().items():
        assert "GITHUB_STEP_SUMMARY" in _runs(job), name


def test_no_pull_request_or_manual_trigger_and_no_hard_coded_owner() -> None:
    for step in steps_of(_wf()):
        assert "pull_request" not in str(step.get("if", ""))
    assert set(_wf()["on"]) == {"workflow_call"}
    assert "ghcr.io/" not in str(_wf()).replace("ghcr.io/${{", "")


def test_attestations_run_only_on_public_repos_or_when_enabled_and_signing_always_runs() -> None:
    job = _jobs()["sign-attest"]
    (provenance,) = _uses(job, "actions/attest-build-provenance@")
    (sbom,) = _uses(_jobs()["attest-sbom"], "actions/attest-sbom@")
    for step in (provenance, sbom):
        assert _expr(step) == ATTEST_GUARD
    for needle in ("cosign sign", "cosign verify"):
        assert "if" not in job["steps"][_index(job, needle)]


def test_skipped_attestations_warn_and_say_how_to_enable_them() -> None:
    job = _jobs()["sign-attest"]
    (note,) = [s for s in job["steps"] if "ATTESTATIONS_ENABLED" in str(s.get("run", ""))]
    assert _expr(note) == f"!({ATTEST_GUARD})"
    assert "::warning::" in note["run"]
    assert "GITHUB_STEP_SUMMARY" in note["run"]
    assert "${{" not in note["run"]


PUSH_GUARD = (
    "github.event_name == 'push' && "
    "(github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v'))"
)


def test_every_job_refuses_to_run_outside_a_push_to_main_or_a_v_tag() -> None:
    for name, job in _jobs().items():
        assert _expr({"if": job["if"]}) == PUSH_GUARD, name


def test_the_per_platform_artifacts_are_named_by_image_and_platform() -> None:
    jobs = _jobs()
    names = {
        n: [s["with"]["name"] for s in _uses(jobs[n], "actions/upload-artifact@")]
        for n in ("build", "sbom", "grype")
    }
    assert names == {
        "build": ["platform-${{ matrix.id }}.${{ matrix.slug }}"],
        "sbom": ["sbom-${{ matrix.id }}.${{ matrix.slug }}"],
        "grype": ["grype-${{ matrix.id }}.${{ matrix.slug }}"],
    }


def test_the_digest_step_pipes_with_pipefail() -> None:
    step = next(s for s in _jobs()["build"]["steps"] if "imagetools inspect" in s.get("run", ""))
    assert step["run"].lstrip().startswith("set -euo pipefail")


def test_the_skipped_attestation_note_names_the_visibility_and_how_to_opt_in() -> None:
    job = _jobs()["sign-attest"]
    (note,) = [s for s in job["steps"] if "ATTESTATIONS_ENABLED" in str(s.get("run", ""))]
    assert note["env"] == {"VISIBILITY": "${{ github.event.repository.visibility }}"}
    assert "not public (visibility: ${VISIBILITY})" in note["run"]
    assert "ATTESTATIONS_ENABLED" in note["run"]
    assert "is private" not in note["run"]
