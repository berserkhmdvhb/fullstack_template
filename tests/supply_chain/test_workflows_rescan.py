"""The weekly re-scan workflow: three jobs, least privilege, no interpolation. Static, offline."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

from tests.supply_chain._workflows import REPO, WORKFLOWS, steps_of, unpinned_uses, workflow

FILE = "rescan.yml"
SCRIPT = REPO / "scripts" / "ci" / "rescan-scan.sh"
BASH: str = shutil.which("bash") or "/usr/bin/bash"


def _wf() -> dict[str, Any]:
    return workflow(FILE)


def _jobs() -> dict[str, Any]:
    return cast("dict[str, Any]", _wf()["jobs"])


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def test_triggers_are_a_weekly_off_the_hour_schedule_and_manual_dispatch() -> None:
    on = _wf()["on"]
    assert set(on) == {"schedule", "workflow_dispatch"}
    (entry,) = on["schedule"]
    minute, hour, day, month, weekday = entry["cron"].split()
    assert minute not in {"0", "*"}
    assert hour.isdigit()
    assert (day, month) == ("*", "*")
    assert weekday.isdigit()  # weekly


def test_top_level_permissions_are_empty_and_a_second_run_waits() -> None:
    assert _wf()["permissions"] == {}
    assert _wf()["concurrency"]["cancel-in-progress"] is False
    assert "${{" not in _wf()["concurrency"]["group"]


def test_there_are_exactly_three_jobs_in_a_chain() -> None:
    jobs = _jobs()
    assert list(jobs) == ["collect", "scan", "report"]
    assert jobs["scan"]["needs"] == "collect"
    assert set(jobs["report"]["needs"]) == {"collect", "scan"}


def test_each_job_holds_only_its_own_grant() -> None:
    perms = {n: j["permissions"] for n, j in _jobs().items()}
    assert perms["collect"] == {"contents": "read", "actions": "read", "deployments": "read"}
    assert perms["scan"] == {"contents": "read", "packages": "read"}
    assert perms["report"] == {"contents": "read", "issues": "write"}


def test_only_the_scan_job_reaches_the_registry() -> None:
    for name in ("collect", "report"):
        job = _jobs()[name]
        assert "packages" not in job["permissions"], name
        assert "ghcr.io" not in _runs(job), name
        assert "grype" not in _runs(job), name
    assert "issues" not in _jobs()["scan"]["permissions"]
    report = _jobs()["report"]
    text = str(report)
    assert "packages" not in report["permissions"]
    assert "GRYPE_REGISTRY_AUTH" not in text
    assert "docker/login-action" not in text
    assert "tools-install" not in text


def test_scan_runs_grype_by_digest_with_the_pinned_installer_and_no_vex() -> None:
    job = _jobs()["scan"]
    text = _runs(job) + SCRIPT.read_text(encoding="utf-8")
    assert "infra/security/tools-install.sh grype" in text
    assert ".tools.grype" in text
    assert 'ref="${IMAGE_REPOSITORY}@${IMAGE_DIGEST}"' in text
    assert '"registry:${ref}"' in text
    assert "--vex" not in text  # a release VEX would hide a later re-rating of a fixed-upstream CVE
    assert "VEX_FILE" not in str(job)
    assert "-o json" in text
    assert "((rc != 0 && rc != 2))" in text  # exit 2 is a finding; anything else is an error
    assert job["strategy"]["matrix"] == "${{ fromJSON(needs.collect.outputs.matrix) }}"
    assert job["strategy"]["fail-fast"] is False
    assert job["if"].strip("${} ") == "needs.collect.outputs.count != '0'"


def test_the_registry_token_reaches_grype_only_through_env() -> None:
    step = next(s for s in _jobs()["scan"]["steps"] if s.get("name") == "Scan the digest")
    assert step["env"]["GRYPE_REGISTRY_AUTH_PASSWORD"] == "${{ github.token }}"  # noqa: S105
    assert "github.token" not in step["run"]
    assert "scripts/ci/rescan-scan.sh" in step["run"]
    # docker reads the same token to list the index's platforms, through the same env, on stdin.
    assert "--password-stdin" in _runs(_jobs()["scan"])


INDEX = {
    "manifests": [
        {"platform": {"os": "linux", "architecture": "amd64"}, "digest": "sha256:" + "1" * 64},
        {"platform": {"os": "linux", "architecture": "arm64"}, "digest": "sha256:" + "2" * 64},
        {"platform": {"os": "linux", "architecture": "arm", "variant": "v7"}},
        {"platform": {"os": "unknown", "architecture": "unknown"}},  # an attestation manifest
    ]
}
DOCKER_STUB = """#!/usr/bin/env bash
echo "docker $*" >> "$STUB_LOG"
if [[ -n "${DOCKER_FAILS:-}" ]]; then echo "denied" >&2; exit 1; fi
cat "$INDEX_JSON"
"""
GRYPE_STUB = r"""#!/usr/bin/env bash
echo "grype $*" >> "$STUB_LOG"
platform=""
while (($#)); do
  case "$1" in
    --platform) platform="$2"; shift ;;
    --file) out="$2"; shift ;;
  esac
  shift
done
cp "$GRYPE_DIR/${platform//\//_}.json" "$out"
exit "${GRYPE_RC:-2}"
"""


def _scan(
    tmp_path: Path,
    index: dict[str, Any],
    reports: dict[str, Any],
    rc: int = 2,
    *,
    docker_fails: bool = False,
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("docker", DOCKER_STUB), ("grype", GRYPE_STUB)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    gdir = tmp_path / "reports"
    gdir.mkdir()
    for platform, doc in reports.items():
        (gdir / f"{platform.replace('/', '_')}.json").write_text(json.dumps(doc))
    (tmp_path / "index.json").write_text(json.dumps(index))
    return subprocess.run(
        [BASH, str(SCRIPT)],
        cwd=tmp_path,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "STUB_LOG": str(tmp_path / "log"),
            "INDEX_JSON": str(tmp_path / "index.json"),
            "GRYPE_DIR": str(gdir),
            "GRYPE_RC": str(rc),
            "DOCKER_FAILS": "1" if docker_fails else "",
            "IMAGE_REPOSITORY": "ghcr.io/o/r/app",
            "IMAGE_DIGEST": "sha256:" + "f" * 64,
            "IMAGE_SHORT": "app-ffffff",
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
            "GRYPE_CONFIG": "cfg.yaml",
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _match(vuln: str, pkg: str, version: str = "1.0") -> dict[str, Any]:
    return {
        "vulnerability": {"id": vuln, "severity": "High"},
        "artifact": {"name": pkg, "version": version, "type": "deb"},
    }


def test_the_rescan_scans_every_platform_of_the_index_and_merges_the_findings(
    tmp_path: Path,
) -> None:
    reports = {
        "linux/amd64": {"matches": [_match("CVE-1", "libc"), _match("CVE-2", "zlib")]},
        "linux/arm64": {"matches": [_match("CVE-2", "zlib"), _match("CVE-3", "arm-only")]},
        "linux/arm/v7": {"matches": []},
    }
    res = _scan(tmp_path, INDEX, reports)
    assert res.returncode == 0, res.stderr
    log = (tmp_path / "log").read_text()
    for platform in ("linux/amd64", "linux/arm64", "linux/arm/v7"):
        assert f"--platform {platform}" in log
    assert "unknown" not in log.split("docker", 2)[-1].replace("imagetools", "")
    merged = json.loads((tmp_path / "findings" / "app-ffffff.json").read_text())
    ids = sorted(m["vulnerability"]["id"] for m in merged["matches"])
    assert ids == ["CVE-1", "CVE-2", "CVE-3"]  # arm64-only CVE kept, shared one once


def test_a_single_manifest_is_scanned_without_a_platform(tmp_path: Path) -> None:
    res = _scan(tmp_path, {"config": {}}, {"": {"matches": [_match("CVE-9", "x")]}})
    assert res.returncode == 0, res.stderr
    assert "--platform" not in (tmp_path / "log").read_text().split("grype", 1)[1]
    merged = json.loads((tmp_path / "findings" / "app-ffffff.json").read_text())
    assert [m["vulnerability"]["id"] for m in merged["matches"]] == ["CVE-9"]


def test_a_grype_error_on_any_platform_fails_the_scan(tmp_path: Path) -> None:
    reports: dict[str, Any] = {"linux/amd64": {"matches": []}, "linux/arm64": {"matches": []}}
    res = _scan(tmp_path, {"manifests": INDEX["manifests"][:2]}, reports, rc=1)
    assert res.returncode != 0
    assert "::error::" in res.stdout + res.stderr


def test_report_runs_after_a_partial_scan_and_never_when_collect_failed() -> None:
    cond = _jobs()["report"]["if"]
    assert "!cancelled()" in cond
    assert "needs.collect.result == 'success'" in cond
    assert "needs.scan.result" not in cond


def test_the_scripts_do_the_work() -> None:
    assert "scripts/rescan_collect.py" in _runs(_jobs()["collect"])
    assert "scripts/rescan_report.py" in _runs(_jobs()["report"])


def test_no_expression_is_interpolated_into_a_run_script() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), (name, step.get("name"))


def test_tokens_are_passed_only_through_env() -> None:
    text = (WORKFLOWS / FILE).read_text(encoding="utf-8")
    for line in text.splitlines():
        if "github.token" in line:
            assert re.match(r"^\s+[A-Z_]+: \$\{\{ github\.token \}\}$", line), line
    assert "secrets." not in text


def test_every_job_has_a_timeout_and_every_action_is_pinned() -> None:
    for name, job in _jobs().items():
        assert isinstance(job["timeout-minutes"], int), name
    assert unpinned_uses(WORKFLOWS / FILE) == []
    assert all(
        "persist-credentials" in s["with"]
        for s in steps_of(_wf())
        if "checkout" in str(s.get("uses"))
    )


def test_nothing_hard_codes_an_owner_or_a_repository() -> None:
    text = (WORKFLOWS / FILE).read_text(encoding="utf-8")
    assert not re.search(r"ghcr\.io/[a-z]", text)
    assert "github.com/" not in text


def test_the_matrix_carries_no_vex_field() -> None:
    step = next(s for s in _jobs()["collect"]["steps"] if s.get("id") == "matrix")
    assert "vex" not in step["run"]


def _failed_closed(tmp_path: Path, res: subprocess.CompletedProcess[str]) -> None:
    assert res.returncode != 0
    out = res.stdout + res.stderr
    assert "::error::" in out
    assert "ghcr.io/o/r/app@sha256:" in out  # names the image
    assert "platform" in out  # and the step
    assert not (tmp_path / "findings" / "app-ffffff.json").exists()
    assert "grype" not in (tmp_path / "log").read_text()  # never falls back to one platform


def test_an_unreadable_platform_list_fails_closed_instead_of_scanning_one_platform(
    tmp_path: Path,
) -> None:
    reports: dict[str, Any] = {"": {"matches": []}, "linux/amd64": {"matches": []}}
    _failed_closed(tmp_path, _scan(tmp_path, INDEX, reports, docker_fails=True))


def test_an_index_that_lists_no_platform_fails_closed(tmp_path: Path) -> None:
    only_attestations = {"manifests": [{"platform": {"os": "unknown", "architecture": "unknown"}}]}
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _failed_closed(tmp_path / "a", _scan(tmp_path / "a", only_attestations, {}))
    _failed_closed(tmp_path / "b", _scan(tmp_path / "b", {"manifests": []}, {}))


def test_a_one_platform_index_is_scanned_for_that_platform(tmp_path: Path) -> None:
    index = {"manifests": INDEX["manifests"][:1] + INDEX["manifests"][3:]}
    res = _scan(tmp_path, index, {"linux/amd64": {"matches": [_match("CVE-5", "x")]}})
    assert res.returncode == 0, res.stderr
    log = (tmp_path / "log").read_text()
    assert log.count("grype ") == 1
    assert "--platform linux/amd64" in log
