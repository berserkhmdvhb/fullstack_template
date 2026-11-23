"""preview.yml: optional pull request previews on dev (wiki/ci-cd.md, Pull request previews).

Static, offline: the trigger, the fork and label conditions, the off-by-default variable, the
permissions per job, the environment, and that the deploy and cleanup go through the shared scripts.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.supply_chain._workflows import REPO, WORKFLOWS, load, workflow

PATH = WORKFLOWS / "preview.yml"
DEPLOY_JOBS = ("digests", "deploy")
CLUSTER_JOBS = ("deploy", "cleanup", "sweep")
ENABLED = "vars.PREVIEWS_ENABLED == 'true'"
SAME_REPO = "github.event.pull_request.head.repo.id == github.event.pull_request.base.repo.id"
LABELLED = "contains(github.event.pull_request.labels.*.name, 'preview')"


def _wf() -> dict[str, Any]:
    return workflow("preview.yml")


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = _wf()["jobs"]
    return jobs


def _if(job: str) -> str:
    return " ".join(str(_jobs()[job]["if"]).split())


def _step(job: str, name: str) -> dict[str, Any]:
    step: dict[str, Any] = next(s for s in _jobs()[job]["steps"] if s.get("name") == name)
    return step


def test_the_trigger_is_pull_request_and_a_daily_schedule_never_the_base_context_one() -> None:
    on = _wf()["on"]
    assert set(on) == {"pull_request", "schedule"}
    assert set(on["pull_request"]["types"]) == {
        "labeled",
        "unlabeled",
        "synchronize",
        "reopened",
        "closed",
    }
    assert len(on["schedule"]) == 1
    assert "pull_request_target" not in PATH.read_text(encoding="utf-8")


def test_every_job_is_off_unless_previews_are_enabled() -> None:
    for name in _jobs():
        assert _if(name).startswith(f"{ENABLED} && github.event_name =="), name


def test_the_deploy_runs_only_for_a_labelled_same_repository_pull_request() -> None:
    for name in DEPLOY_JOBS:
        cond = _if(name)
        assert "github.event_name == 'pull_request'" in cond
        assert SAME_REPO in cond
        assert LABELLED in cond
        assert "github.event.action != 'closed' && github.event.action != 'unlabeled'" in cond
        # Another label added to a previewed pull request deploys nothing.
        assert "(github.event.action != 'labeled' || github.event.label.name == 'preview')" in cond
    assert _jobs()["deploy"]["needs"] == ["digests"]
    assert _if("digests") == _if("deploy")


def test_closing_or_unlabelling_removes_the_preview_and_a_fork_never_reaches_the_cluster() -> None:
    cond = _if("cleanup")
    assert SAME_REPO in cond
    assert f"(github.event.action == 'closed' && {LABELLED})" in cond
    assert "(github.event.action == 'unlabeled' && github.event.label.name == 'preview')" in cond
    for name in CLUSTER_JOBS[:2]:
        assert SAME_REPO in _if(name), name


def test_the_sweep_runs_on_the_schedule_only() -> None:
    for name in ("open-prs", "sweep"):
        assert _if(name) == f"{ENABLED} && github.event_name == 'schedule'", name
    assert _jobs()["sweep"]["needs"] == ["open-prs"]


@pytest.mark.parametrize(
    ("job", "grants"),
    [
        ("digests", {"contents": "read", "actions": "read", "deployments": "read"}),
        ("deploy", {"contents": "read", "id-token": "write"}),
        ("cleanup", {"contents": "read", "id-token": "write"}),
        ("open-prs", {"contents": "read", "pull-requests": "read"}),
        ("sweep", {"contents": "read", "id-token": "write"}),
    ],
)
def test_each_job_holds_only_what_it_needs(job: str, grants: dict[str, str]) -> None:
    assert _wf()["permissions"] == {}
    assert _jobs()[job]["permissions"] == grants
    assert "pull-requests: write" not in PATH.read_text(encoding="utf-8")  # no comment is posted


def test_every_cluster_job_uses_the_preview_environment_and_no_other_job_sees_a_secret() -> None:
    for name, job in _jobs().items():
        env = job.get("environment")
        if name in CLUSTER_JOBS:
            assert env["name"] == "preview", name
            assert any(s.get("uses") == "./.github/actions/cluster-access" for s in job["steps"])
        else:
            assert env is None, name
            assert "secrets." not in str(job), name
    assert _jobs()["deploy"]["environment"]["url"] == "${{ steps.deploy.outputs.url }}"


def test_the_access_token_reaches_only_the_deploy_step_as_env() -> None:
    for name, job in _jobs().items():
        for step in job["steps"]:
            text = str(step)
            if "CF_ACCESS" in text:
                assert (name, step.get("name")) == ("deploy", "Deploy, verify and smoke test")
                for key in ("CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET"):
                    assert step["env"][key] == f"${{{{ secrets.{key} }}}}"
                assert "secrets." not in step["run"]


def test_the_deploy_is_deploy_env_sh_with_the_digests_dev_runs() -> None:
    env = _jobs()["deploy"]["env"]
    assert env["DEPLOY_ENV"] == "preview"
    assert env["PREVIEW_PR"] == "${{ github.event.pull_request.number }}"
    assert env["DIGESTS"] == "${{ needs.digests.outputs.digests }}"
    assert env["HELM_TIMEOUT"] == "${{ vars.HELM_TIMEOUT || '5m' }}"
    step = _step("deploy", "Deploy, verify and smoke test")
    assert step["id"] == "deploy"
    assert step["run"] == "bash scripts/ci/deploy-env.sh"
    assert step["env"]["PREVIEW_DOMAIN"] == "${{ vars.PREVIEW_DOMAIN }}"
    # The digests come from dev's deployment record (release.yml's artifact), never a PR build.
    run = _step("digests", "Read the digests dev runs")["run"]
    assert "scripts/rescan_collect.py --deployed dev" in run
    text = PATH.read_text(encoding="utf-8")
    for word in ("docker build", "build-image.sh", "docker push", "cosign", "attest", "cache"):
        assert word not in text, word
    shared = workflow("deploy.yml")["env"]
    assert {k: _wf()["env"][k] for k in shared} == shared  # the deploy's own names and ports


def test_the_cleanup_and_the_sweep_are_preview_cleanup_sh() -> None:
    assert _step("cleanup", "Uninstall the release")["run"] == (
        'bash scripts/ci/preview-cleanup.sh --pr "${PREVIEW_PR}"'
    )
    assert _jobs()["cleanup"]["env"]["PREVIEW_PR"] == "${{ github.event.pull_request.number }}"
    sweep = _jobs()["sweep"]
    assert _step("sweep", "Remove closed and expired previews")["run"] == (
        "bash scripts/ci/preview-cleanup.sh --sweep"
    )
    assert sweep["env"]["OPEN_PRS"] == "${{ needs.open-prs.outputs.numbers }}"
    assert sweep["env"]["PREVIEW_TTL_DAYS"] == "${{ vars.PREVIEW_TTL_DAYS || '7' }}"
    assert "gh pr list --state open" in _step("open-prs", "List the open pull requests")["run"]


def test_a_deploy_and_a_cleanup_of_one_pull_request_never_overlap() -> None:
    group = _wf()["concurrency"]
    assert group == {
        "group": "preview-${{ github.event.pull_request.number || 'sweep' }}",
        "cancel-in-progress": False,
    }


def test_no_expression_is_interpolated_into_a_run_script() -> None:
    for job in _jobs().values():
        for step in job["steps"]:
            assert not re.search(r"\$\{\{", str(step.get("run", ""))), step


def test_the_workflow_is_documented_and_the_variable_listed() -> None:
    docs = (REPO / "wiki" / "ci-cd.md").read_text(encoding="utf-8")
    assert "## Pull request previews" in docs
    for word in ("PREVIEWS_ENABLED", "PREVIEW_DOMAIN", "PREVIEW_TTL_DAYS", "CF_ACCESS_CLIENT_ID"):
        assert word in docs, word
    assert load(PATH)["name"] == "Preview"
