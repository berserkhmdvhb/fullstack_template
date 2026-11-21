"""The weekly re-scan workflow: three jobs, least privilege, no interpolation. Static, offline."""

from __future__ import annotations

import re
from typing import Any, cast

from tests.supply_chain._workflows import WORKFLOWS, steps_of, unpinned_uses, workflow

FILE = "rescan.yml"


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
    assert "scan" not in "".join(
        s.get("run", "") for s in _jobs()["report"]["steps"] if "tools-install" in s.get("run", "")
    )


def test_scan_runs_grype_by_digest_with_the_pinned_installer_and_the_release_vex() -> None:
    job = _jobs()["scan"]
    text = _runs(job)
    assert "infra/security/tools-install.sh grype" in text
    assert ".tools.grype" in text
    assert 'registry:${IMAGE_REPOSITORY}@${IMAGE_DIGEST}"' in text
    assert "--vex" in text
    assert "-o json" in text
    assert "(( rc != 0 && rc != 2 ))" in text  # exit 2 is a finding; anything else is an error
    assert job["strategy"]["matrix"] == "${{ fromJSON(needs.collect.outputs.matrix) }}"
    assert job["strategy"]["fail-fast"] is False
    assert job["if"].strip("${} ") == "needs.collect.outputs.count != '0'"


def test_the_registry_token_reaches_grype_only_through_env() -> None:
    step = next(s for s in _jobs()["scan"]["steps"] if s.get("name") == "Scan the digest")
    assert step["env"]["GRYPE_REGISTRY_AUTH_PASSWORD"] == "${{ github.token }}"  # noqa: S105
    assert "github.token" not in step["run"]


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
