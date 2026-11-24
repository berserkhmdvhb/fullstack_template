"""The required status checks: the doc names real jobs, and the `ci-ok` aggregate gate behaves."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

import pytest

from tests.supply_chain._workflows import REPO, WORKFLOWS, load, workflow

DOC = REPO / "wiki" / "github-repository-settings.md"
SCRIPT = REPO / "scripts" / "github-settings.sh"
BASH: str = shutil.which("bash") or "/usr/bin/bash"
CI_OK_MAX_TIMEOUT_MINUTES = 10
MIN_TABLE_CELLS = 3  # a variable row: name, "variable", value


def _section(heading: str) -> str:
    text = DOC.read_text(encoding="utf-8")
    pattern = rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, text, re.DOTALL | re.MULTILINE)
    assert match, f"{DOC.name} has no '## {heading}' section"
    return match.group(1)


def _listed(heading: str) -> list[str]:
    """The backticked names in the bullets of a section."""
    return re.findall(r"^- `([^`]+)`", _section(heading), re.MULTILINE)


def _job_names() -> set[str]:
    names: set[str] = set()
    for path in WORKFLOWS.glob("*.yml"):
        for job in load(path).get("jobs", {}).values():
            if "name" in job:
                names.add(job["name"])
    return names


def _is_advisory(job: dict[str, Any]) -> bool:
    """Advisory only when the name says so or continue-on-error is literally true.

    A conditional continue-on-error (one matrix leg, or only on pull requests) is not an exemption.
    """
    return (
        job.get("name", "").lower().rstrip().endswith("(advisory)")
        or job.get("continue-on-error") is True
    )


def test_required_checks_are_real_job_names() -> None:
    required = _listed("Required status checks")
    assert required == ["CI OK"]
    assert set(required) <= _job_names()


def test_no_advisory_job_is_required() -> None:
    required = set(_listed("Required status checks"))
    for path in WORKFLOWS.glob("*.yml"):
        for job in load(path).get("jobs", {}).values():
            if _is_advisory(job):
                assert job.get("name") not in required
    assert not {n for n in required if "advisory" in n.lower() or n.startswith("Live")}


def test_advisory_checks_are_documented_as_not_required() -> None:
    assert _listed("Advisory checks (never required)")


def test_ci_ok_needs_every_blocking_job() -> None:
    jobs = workflow("ci.yml")["jobs"]
    gate = jobs["ci-ok"]
    assert gate["name"] == "CI OK"
    assert set(gate["needs"]) == set(jobs) - {"ci-ok"}
    assert gate["if"].strip() == "always()"
    assert gate["permissions"] == {"contents": "read"}
    assert gate["timeout-minutes"] <= CI_OK_MAX_TIMEOUT_MINUTES


def test_live_pr_failure_cannot_fail_the_gate() -> None:
    # `ci-ok` ignores `live` on pull requests (TestGateScript), and the job still fails soft
    # inside its own workflow, so the check list stays clean; on main and tags it is blocking.
    live = workflow("reusable-live.yml")["jobs"]["live"]
    assert live["continue-on-error"] == "${{ github.event_name == 'pull_request' }}"


def _gate(results: dict[str, str], event: str = "pull_request") -> int:
    script = workflow("ci.yml")["jobs"]["ci-ok"]["steps"][-1]["run"]
    needs = json.dumps({k: {"result": v, "outputs": {}} for k, v in results.items()})
    env = {"NEEDS": needs, "EVENT_NAME": event, "PATH": os.environ["PATH"]}
    return subprocess.run(
        [BASH, "-c", script], env=env, capture_output=True, check=False
    ).returncode


def _all(result: str = "success", **override: str) -> dict[str, str]:
    jobs = set(workflow("ci.yml")["jobs"]) - {"ci-ok"}
    return dict.fromkeys(jobs, result) | override


def test_every_blocking_pull_request_job_is_gated() -> None:
    required = set(_listed("Required status checks"))
    for path in WORKFLOWS.glob("*.yml"):
        doc = load(path)
        # preview.yml deploys a labelled pull request's preview; it is not a check of the code.
        # pr-title.yml lints the title, which an edit changes without a commit: CI OK (ci.yml does
        # not run on edits) cannot wait on it, and it reads no code.
        # pr-agent.yml is the optional AI reviewer: advisory by design, off by default (ADR-0040).
        exempt = ("preview.yml", "pr-title.yml", "pr-agent.yml")
        if "pull_request" not in doc["on"] or path.name in exempt:
            continue
        needs = set(doc["jobs"].get("ci-ok", {}).get("needs", []))
        for key, job in doc["jobs"].items():
            if _is_advisory(job) or job.get("name") in required:
                continue
            assert key in needs, (
                f"{path.name}: job {key} is blocking on pull requests but not behind CI OK"
            )


def test_no_workflow_uses_pull_request_target() -> None:
    for path in [*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]:
        assert "pull_request_target" not in load(path)["on"], path.name


def test_the_chart_lint_is_behind_the_gate_and_called_before_deploys() -> None:
    assert "chart" in workflow("ci.yml")["jobs"]["ci-ok"]["needs"]
    assert "helm-checks" in workflow("deploy.yml")["jobs"]["deploy-dev"]["needs"]


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is needed to run the gate script")
class TestGateScript:
    def test_all_success_passes(self) -> None:
        assert _gate(_all()) == 0
        assert _gate(_all(), "push") == 0

    def test_filtered_jobs_may_be_skipped_on_a_pull_request(self) -> None:
        skipped = _all(backend="skipped", frontend="skipped", live="skipped", pins="skipped")
        assert _gate(skipped) == 0

    @pytest.mark.parametrize("job", ["backend", "frontend", "live", "pins"])
    def test_skips_fail_off_a_pull_request(self, job: str) -> None:
        assert _gate(_all(**{job: "skipped"}), "push") != 0

    @pytest.mark.parametrize("job", ["chart", "security", "repo", "workflows", "hadolint"])
    def test_unfilterable_jobs_may_not_be_skipped(self, job: str) -> None:
        assert _gate(_all(**{job: "skipped"})) != 0

    @pytest.mark.parametrize("bad", ["failure", "cancelled"])
    def test_failed_or_cancelled_fails(self, bad: str) -> None:
        assert _gate(_all(repo=bad)) != 0
        assert _gate(_all(backend=bad)) != 0

    def test_live_is_ignored_on_a_pull_request_only(self) -> None:
        assert _gate(_all(live="failure")) == 0
        assert _gate(_all(live="cancelled")) == 0
        assert _gate(_all(live="failure"), "push") != 0

    def test_skipped_changes_fails(self) -> None:
        assert _gate(_all(changes="skipped")) != 0


def _script_list(name: str) -> set[str]:
    match = re.search(rf'^{name}="([^"]*)"', SCRIPT.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, name
    return set(match.group(1).split())


def _table() -> dict[str, str]:
    """Variable name -> scope cell, from the variables table of platform-setup.md (read only)."""
    text = (REPO / "wiki" / "platform-setup.md").read_text(encoding="utf-8")
    text = text.split("Not wired up yet")[0]
    out: dict[str, str] = {}
    for row in re.findall(r"^\|(.+)$", text, re.MULTILINE):
        cells = [c.strip() for c in row.split("|")]
        if len(cells) >= MIN_TABLE_CELLS and cells[1] == "variable":
            for name in re.findall(r"`([A-Z][A-Z0-9_]+)`", cells[0]):
                out[name] = cells[2]
    return out


def test_script_variable_lists_match_the_platform_setup_table() -> None:
    table = _table()
    repo_vars, env_vars = _script_list("REPO_VARS"), _script_list("ENV_VARS")
    repo_scoped = {n for n, scope in table.items() if "repository" in scope}
    # The opt-in `preview` environment is created by hand, not by the script (not one of its ENVS).
    env_scoped = {
        n for n, scope in table.items() if "environment" in scope and "`preview`" not in scope
    }
    assert "PREVIEW_DOMAIN" in set(table) - env_scoped
    assert repo_scoped <= repo_vars
    assert env_scoped <= env_vars
    assert repo_vars <= set(table)
    assert env_vars <= set(table)


def test_script_required_check_equals_the_doc_list() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'^REQUIRED_CHECK="([^"]*)"', script, re.MULTILINE)
    assert match
    assert _listed("Required status checks") == [match.group(1)]
