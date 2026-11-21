"""The `api-compat` job in ci.yml (the oasdiff gate), and label events, which must not skip jobs."""

from __future__ import annotations

import re
from typing import Any, cast

from tests.supply_chain._workflows import WORKFLOWS, load, workflow

LABEL = "breaking-change"
FIRST_PARENT_DEPTH = 2
LABEL_EVENTS = ("labeled", "unlabeled")


def job() -> dict[str, Any]:
    jobs: dict[str, Any] = workflow("ci.yml")["jobs"]
    assert "api-compat" in jobs
    return cast("dict[str, Any]", jobs["api-compat"])


def test_label_events_trigger_the_whole_workflow() -> None:
    types = workflow("ci.yml")["on"]["pull_request"]["types"]
    assert set(types) == {"opened", "synchronize", "reopened", *LABEL_EVENTS}


def test_no_job_is_conditioned_on_the_event_action_or_a_label() -> None:
    """A label event must run every job, or `CI OK` could pass over failing tests."""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, j in load(path).get("jobs", {}).items():
            condition = str(j.get("if", ""))
            assert not re.search(r"event\.action|labeled|label", condition), f"{path.name}:{name}"
            for step in j.get("steps", []):
                step_if = str(step.get("if", ""))
                assert not re.search(r"event\.action|labeled|label", step_if), f"{path.name}:{name}"


def test_the_job_is_blocking_least_privilege_and_always_runs() -> None:
    j = job()
    assert "continue-on-error" not in j
    assert "if" not in j, "the script skips pushes itself; a skipped job would hide the gate"
    assert "needs" not in j
    assert j["permissions"] == {"contents": "read"}
    assert "timeout-minutes" in j
    assert j["steps"][0]["with"]["persist-credentials"] is False
    assert j["steps"][0]["with"]["fetch-depth"] == FIRST_PARENT_DEPTH, (
        "the first parent must be present"
    )


def test_the_job_is_behind_ci_ok_and_not_skippable() -> None:
    assert "api-compat" in workflow("ci.yml")["jobs"]["ci-ok"]["needs"]


def test_the_label_reaches_the_script_through_env_never_through_run() -> None:
    steps = job()["steps"]
    step = next(s for s in steps if "oasdiff.sh" in s.get("run", ""))
    assert step["run"].strip() == "bash scripts/ci/oasdiff.sh"
    assert "${{" not in step["run"]
    assert step["env"]["BREAKING_CHANGE_LABEL"] == (
        f"${{{{ contains(github.event.pull_request.labels.*.name, '{LABEL}') }}}}"
    )
    assert step["env"]["EVENT_NAME"] == "${{ github.event_name }}"
    assert "BASE_REF" not in step["env"], "the base is the merge commit's first parent"


def test_the_job_installs_the_pinned_binary() -> None:
    runs = "\n".join(s.get("run", "") for s in job()["steps"])
    assert "scripts/tools.sh install oasdiff" in runs
