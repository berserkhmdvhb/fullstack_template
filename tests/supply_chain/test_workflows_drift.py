"""The weekly live-drift workflow: three grants, read-only cluster access, a fork skips. Static."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

from tests.supply_chain._workflows import WORKFLOWS, steps_of, unpinned_uses, workflow

FILE = "drift.yml"
ENVS = ["dev", "qa", "production"]
BASH: str = shutil.which("bash") or "/usr/bin/bash"


def _wf() -> dict[str, Any]:
    return workflow(FILE)


def _jobs() -> dict[str, Any]:
    return cast("dict[str, Any]", _wf()["jobs"])


def _step(job: str, name: str) -> dict[str, Any]:
    return next(s for s in _jobs()[job]["steps"] if s.get("name") == name)


def test_triggers_are_a_weekly_schedule_and_manual_dispatch_never_a_pull_request() -> None:
    on = _wf()["on"]
    assert set(on) == {"schedule", "workflow_dispatch"}
    (entry,) = on["schedule"]
    minute, hour, day, month, weekday = entry["cron"].split()
    assert minute not in {"0", "*"}
    assert hour.isdigit()
    assert (day, month) == ("*", "*")
    assert weekday.isdigit()
    assert "pull_request" not in (WORKFLOWS / FILE).read_text(encoding="utf-8")


def test_top_level_permissions_are_empty_and_a_second_run_waits() -> None:
    assert _wf()["permissions"] == {}
    assert _wf()["concurrency"] == {"group": "drift", "cancel-in-progress": False}


def test_the_jobs_and_their_grants() -> None:
    jobs = _jobs()
    assert list(jobs) == ["collect", "drift", "report"]
    assert jobs["collect"]["permissions"] == {
        "contents": "read",
        "actions": "read",  # the release run's digests artifact, and the deploying job and run
        "deployments": "read",
    }
    assert jobs["drift"]["permissions"] == {"contents": "read", "id-token": "write"}
    assert jobs["report"]["permissions"] == {"contents": "read", "issues": "write"}
    for name, job in jobs.items():
        assert isinstance(job["timeout-minutes"], int), name


def test_one_drift_job_per_environment_in_its_own_drift_environment() -> None:
    job = _jobs()["drift"]
    assert job["needs"] == "collect"
    assert job["strategy"] == {"fail-fast": False, "matrix": {"environment": ENVS}}
    # The environment's variables and branch policy, but no deployment record: a drift run deploys
    # nothing, so it must never look like one (the re-scan reads the deployment records).
    assert job["environment"] == {"name": "drift-${{ matrix.environment }}", "deployment": False}


def test_only_the_drift_job_logs_in_and_only_with_its_own_identity() -> None:
    for name in ("collect", "report"):
        text = str(_jobs()[name])
        assert "cluster-access" not in text, name
        assert "id-token" not in text, name
        assert "environment" not in _jobs()[name], name
    access = _step("drift", "Connect to the cluster")
    assert access["uses"] == "./.github/actions/cluster-access"
    assert access["with"]["mode"] == "gke"
    assert access["with"]["wif_provider"] == "${{ vars.DRIFT_WIF_PROVIDER }}"
    assert access["with"]["service_account"] == "${{ vars.DRIFT_SA }}"
    assert "DEPLOYER" not in str(_jobs()["drift"])
    assert "kubeconfig_b64" not in access["with"]


def test_the_deployed_commit_is_rendered_before_the_cluster_login() -> None:
    names = [s.get("name") for s in _jobs()["drift"]["steps"]]
    assert names.index("Render the deployed commit") < names.index("Connect to the cluster")
    assert names.index("Connect to the cluster") < names.index("Compare with the cluster")
    render = _step("drift", "Render the deployed commit")
    assert "scripts/ci/drift-render.sh" in render["run"]
    compare = _step("drift", "Compare with the cluster")
    assert "scripts/drift.py" in compare["run"]
    checkout = next(
        s
        for s in _jobs()["drift"]["steps"]
        if "checkout" in str(s.get("uses")) and "with" in s and "path" in s["with"]
    )
    assert checkout["with"]["path"] == "deployed"
    assert "fromJSON(needs.collect.outputs.deployments)" in checkout["with"]["ref"]


def test_every_later_step_runs_only_when_the_gate_says_so() -> None:
    steps = _jobs()["drift"]["steps"]
    gate = next(i for i, s in enumerate(steps) if s.get("id") == "gate")
    for step in steps[gate + 1 :]:
        assert "steps.gate.outputs.run == 'true'" in step["if"], step.get("name")


GATE_ENV = {
    "DRIFT_WIF_PROVIDER": "projects/1/locations/global/workloadIdentityPools/github/providers/p",
    "DRIFT_SA": "gh-drift-dev@your-project.iam.gserviceaccount.com",
    "DEPLOYED": '{"sha": "' + "a" * 40 + '", "ref": "main", "digests": {}}',
    "ENVIRONMENT": "dev",
    "REF": "refs/heads/main",
}


def _gate(tmp_path: Path, **change: str) -> tuple[str, str]:
    step = _step("drift", "Say whether this environment is checked")
    assert set(step["env"]) == set(GATE_ENV)
    out, summary = tmp_path / "out", tmp_path / "summary"
    env = {**GATE_ENV, **change, "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(summary)}
    env["PATH"] = "/usr/bin:/bin"
    done = subprocess.run(
        [BASH, "-c", step["run"]], env=env, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return out.read_text(), summary.read_text() if summary.exists() else ""


def test_a_fresh_fork_without_the_drift_variables_skips_with_a_summary_line(tmp_path: Path) -> None:
    out, summary = _gate(tmp_path, DRIFT_WIF_PROVIDER="", DRIFT_SA="")
    assert out == "run=false\n"
    assert "drift-dev: skipped" in summary
    assert "DRIFT_WIF_PROVIDER" in summary
    assert "DRIFT_SA" in summary


def test_an_environment_with_no_comparable_deployment_skips_with_its_reason(tmp_path: Path) -> None:
    out, summary = _gate(tmp_path, DEPLOYED='{"skip": "no successful deployment"}')
    assert out == "run=false\n"
    assert "drift-dev: skipped: no successful deployment" in summary


def test_a_run_off_main_skips(tmp_path: Path) -> None:
    out, summary = _gate(tmp_path, REF="refs/heads/feature")
    assert out == "run=false\n"
    assert "main" in summary


def test_a_configured_environment_runs(tmp_path: Path) -> None:
    out, _ = _gate(tmp_path)
    assert out == "run=true\n"


def test_findings_never_fail_the_run_and_the_report_needs_a_good_collection() -> None:
    report = _jobs()["report"]
    assert set(report["needs"]) == {"collect", "drift"}
    assert "!cancelled()" in report["if"]
    assert "needs.collect.result == 'success'" in report["if"]
    assert "scripts/drift_report.py" in "\n".join(s.get("run", "") for s in report["steps"])
    assert "continue-on-error" not in str(_jobs())


def test_no_expression_or_secret_reaches_a_run_script() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), (name, step.get("name"))
    text = (WORKFLOWS / FILE).read_text(encoding="utf-8")
    assert "secrets." not in text
    for line in text.splitlines():
        if "github.token" in line:
            assert re.match(r"^\s+[A-Z_]+: \$\{\{ github\.token \}\}$", line), line


def test_every_action_is_pinned_and_no_checkout_keeps_credentials() -> None:
    assert unpinned_uses(WORKFLOWS / FILE) == []
    for step in steps_of(_wf()):
        if "checkout" in str(step.get("uses")):
            assert step["with"]["persist-credentials"] is False
