"""The deploy workflows: helm checks, guarded environment chain, cluster access, ops.

Static, offline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from tests.supply_chain._workflows import ACTIONS, REPO, WORKFLOWS, load, steps_of, workflow

if TYPE_CHECKING:
    from collections.abc import Callable

DEPLOY_ENVIRONMENT_JOBS = 4  # jobs that log in to the cluster
SHA256_HEX_LENGTH = 64
COMPOSITE = "./.github/actions/cluster-access"
DEPLOY_JOBS = ("deploy-dev", "deploy-qa", "deploy-production")
GUARDS = ("github.event_name == 'push'", "inputs.digests != ''", "vars.DEPLOY_ENABLED == 'true'")


def _dep() -> dict[str, Any]:
    return workflow("deploy.yml")


def _jobs() -> dict[str, Any]:
    return cast("dict[str, Any]", _dep()["jobs"])


def _ops() -> dict[str, Any]:
    return workflow("deploy-ops.yml")


def _composite() -> dict[str, Any]:
    return load(ACTIONS / "cluster-access" / "action.yml")


def _if(job: dict[str, Any]) -> str:
    return str(job.get("if", ""))


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def _uses_cluster_access(job: dict[str, Any]) -> bool:
    return any(s.get("uses") == COMPOSITE for s in job.get("steps", []))


def test_triggers() -> None:
    assert set(_dep()["on"]) == {"workflow_call"}
    assert {"version", "image_tag", "immutable", "digests"} <= set(
        _dep()["on"]["workflow_call"]["inputs"]
    )
    assert set(_ops()["on"]) == {"workflow_dispatch"}


@pytest.mark.parametrize("wf", [_dep, _ops])
def test_top_level_permissions_and_concurrency(wf: Callable[[], dict[str, Any]]) -> None:
    doc = wf()
    assert doc["permissions"] == {}
    assert doc["concurrency"]["cancel-in-progress"] is False
    assert "github.workflow" not in doc["concurrency"]["group"]
    assert doc["concurrency"]["group"].startswith("deploy")


def test_deploy_workflow_group_is_the_literal_prefix() -> None:
    assert _dep()["concurrency"]["group"] == "deploy-${{ github.ref }}-${{ github.event_name }}"


def test_every_deploy_job_has_its_own_environment_group_and_timeout() -> None:
    for name in DEPLOY_JOBS:
        job = _jobs()[name]
        env = name.removeprefix("deploy-")
        assert job["environment"] == env or job["environment"]["name"] == env
        assert job["concurrency"] == {"group": f"deploy-{env}", "cancel-in-progress": False}
        assert isinstance(job["timeout-minutes"], int)


@pytest.mark.parametrize("name", DEPLOY_JOBS)
def test_no_deploy_job_can_run_on_a_pull_request(name: str) -> None:
    cond = _if(_jobs()[name])
    for guard in GUARDS:
        assert guard in cond, (name, guard)
    assert "pull_request" not in cond


def test_the_chain_dev_then_qa_then_production() -> None:
    jobs = _jobs()
    assert "helm-checks" in jobs["deploy-dev"]["needs"]
    assert "deploy-dev" in jobs["deploy-qa"]["needs"]
    assert {"deploy-dev", "deploy-qa"} <= set(jobs["deploy-production"]["needs"])


def test_qa_can_be_dropped_by_deploy_envs() -> None:
    cond = _if(_jobs()["deploy-qa"])
    assert "contains(format(',{0},', vars.DEPLOY_ENVS), ',qa,')" in cond
    assert "vars.DEPLOY_ENVS == ''" in cond
    assert "needs.deploy-dev.result == 'success'" in cond
    assert "!cancelled()" in cond


def test_production_runs_only_on_v_tags_that_are_immutable() -> None:
    cond = _if(_jobs()["deploy-production"])
    for part in (
        "github.ref_type == 'tag'",
        "startsWith(github.ref_name, 'v')",
        "inputs.immutable == 'true'",
        "needs.deploy-dev.result == 'success'",
        "needs.deploy-qa.result == 'success'",
        "needs.deploy-qa.result == 'skipped'",
        "!cancelled()",
    ):
        assert part in cond, part


def test_helm_checks_is_the_call_to_the_reusable_chart_workflow() -> None:
    job = _jobs()["helm-checks"]
    assert job["uses"] == "./.github/workflows/reusable-chart.yml"
    assert job["permissions"] == {"contents": "read"}
    assert "if" not in job


def test_the_chart_workflow_runs_without_environment_secrets_or_id_token() -> None:
    doc = workflow("reusable-chart.yml")
    assert set(doc["on"]) == {"workflow_call"}
    assert doc["permissions"] == {}
    job = doc["jobs"]["chart"]
    assert "environment" not in job
    assert job["permissions"] == {"contents": "read"}
    assert "if" not in job
    assert not _uses_cluster_access(job)
    text = str(job)
    assert "secrets." not in text
    assert "id-token" not in text
    runs = _runs(job)
    assert "scripts/tools.sh install helm4" in runs
    assert "helm lint" in runs
    assert "helm template" in runs
    assert "values.*.yaml" in runs


def test_the_skip_notice_is_written_when_deploys_are_off() -> None:
    job = _jobs()["deploy-notice"]
    assert _if(job) == "github.event_name == 'push'"
    step = job["steps"][-1]
    assert step["env"]["DEPLOY_ENABLED"] == "${{ vars.DEPLOY_ENABLED }}"
    assert "::notice" in step["run"]
    assert "GITHUB_STEP_SUMMARY" in step["run"]
    assert "DEPLOY_ENABLED" in step["run"]
    assert "id-token" not in str(job)
    assert "environment" not in job


def test_every_job_that_authenticates_declares_an_environment() -> None:
    seen = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, job in load(path)["jobs"].items():
            auth = _uses_cluster_access(job) or any(
                str(s.get("uses", "")).startswith("google-github-actions/auth@")
                for s in job.get("steps", [])
            )
            if auth:
                seen += 1
                assert "environment" in job, (path.name, name)
                assert job["permissions"].get("id-token") == "write", (path.name, name)
    assert seen == DEPLOY_ENVIRONMENT_JOBS


def test_only_wif_and_signing_jobs_hold_id_token() -> None:
    holders = {
        (p.name, n)
        for p in sorted(WORKFLOWS.glob("*.yml"))
        for n, j in load(p)["jobs"].items()
        if "id-token" in j["permissions"]
    }
    assert holders == {
        ("release.yml", "deploy"),
        ("release.yml", "images"),  # the union grant for reusable-build.yml
        ("reusable-build.yml", "sign-attest"),  # keyless signing, no cloud login
        ("reusable-build.yml", "attest-sbom"),  # keyless SBOM attestation, no cloud login
        ("deploy.yml", "deploy-dev"),
        ("deploy.yml", "deploy-qa"),
        ("deploy.yml", "deploy-production"),
        ("deploy-ops.yml", "ops"),
        ("scorecard.yml", "analysis"),  # OIDC token for the published Scorecard results
    }


def test_gke_mode_uses_the_per_environment_service_account_and_the_dns_endpoint() -> None:
    steps = _composite()["runs"]["steps"]
    auth = next(
        s for s in steps if str(s.get("uses", "")).startswith("google-github-actions/auth@")
    )
    gke = next(
        s
        for s in steps
        if str(s.get("uses", "")).startswith("google-github-actions/get-gke-credentials@")
    )
    assert auth["with"]["workload_identity_provider"] == "${{ inputs.wif_provider }}"
    assert auth["with"]["service_account"] == "${{ inputs.service_account }}"
    assert gke["with"]["use_dns_based_endpoint"] is True
    for s in (auth, gke):
        assert "inputs.mode == 'gke'" in s["if"]
    for name in (*DEPLOY_JOBS, "ops"):
        job = _jobs().get(name) or _ops()["jobs"][name]
        use = next(s for s in job["steps"] if s.get("uses") == COMPOSITE)
        assert use["with"]["wif_provider"] == "${{ vars.GCP_WIF_PROVIDER }}"
        assert use["with"]["service_account"] == "${{ vars.GCP_DEPLOYER_SA }}"
        assert use["with"]["mode"] == "${{ vars.DEPLOY_MODE || 'gke' }}"
        for key in ("cluster_name", "location", "project_id"):
            assert "vars." in use["with"][key]


def test_kubeconfig_mode_decodes_from_env_into_a_0600_file_and_never_echoes() -> None:
    step = next(s for s in _composite()["runs"]["steps"] if "base64 -d" in s.get("run", ""))
    assert step["env"]["KUBECONFIG_B64"] == "${{ inputs.kubeconfig_b64 }}"
    assert "inputs.mode == 'kubeconfig'" in step["if"]
    run = step["run"]
    assert 'base64 -d <<<"${KUBECONFIG_B64}"' in run
    assert "umask 077" in run
    assert "RUNNER_TEMP" in run
    assert "KUBECONFIG=" in run
    assert 'echo "${KUBECONFIG_B64}"' not in run
    assert "set -x" not in run


def test_the_kubeconfig_secret_reaches_only_a_with_and_is_removed_afterwards() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in load(path)["jobs"].values():
            assert "secrets.KUBECONFIG_B64" not in _runs(job) if "steps" in job else True
    for name in (*DEPLOY_JOBS, "ops"):
        job = _jobs().get(name) or _ops()["jobs"][name]
        use = next(s for s in job["steps"] if s.get("uses") == COMPOSITE)
        assert use["with"]["kubeconfig_b64"] == "${{ secrets.KUBECONFIG_B64 }}"
        last = job["steps"][-1]
        assert last["if"] == "always()"
        assert "rm -f" in last["run"]
        assert "kubeconfig" in last["run"]


def test_no_run_script_interpolates_an_expression() -> None:
    for step in [*steps_of(_composite()), *steps_of(_dep()), *steps_of(_ops())]:
        assert "${{" not in step.get("run", ""), step.get("name")


SCRIPT = (REPO / "scripts/ci/deploy-env.sh").read_text(encoding="utf-8")


def test_each_deploy_job_runs_the_one_deploy_script() -> None:
    for name in DEPLOY_JOBS:
        job = _jobs()[name]
        scripts = [s for s in job["steps"] if "deploy-env.sh" in s.get("run", "")]
        assert len(scripts) == 1, name
        assert scripts[0]["run"].strip() == "bash scripts/ci/deploy-env.sh"
        # The body is not repeated per job any more.
        assert "helm upgrade" not in _runs(job)
        assert "helm rollback" not in _runs(job)
        assert job["env"]["DEPLOY_ENV"] == name.removeprefix("deploy-")
        assert job["env"]["DIGESTS"] == "${{ inputs.digests }}"


def test_the_script_deploys_by_digest_and_rolls_back_to_the_recorded_revision() -> None:
    assert "container_matrix.py" in SCRIPT
    assert "--deploy-matrix --select" in SCRIPT
    assert "^sha256:[0-9a-f]{64}$" in SCRIPT
    assert "--rollback-on-failure" in SCRIPT
    assert "--wait" in SCRIPT
    assert "--set" not in SCRIPT
    assert 'select(.status == "deployed")' in SCRIPT
    assert 'helm rollback "$SERVICE_NAME" "$recorded"' in SCRIPT
    assert "helm rollback" in SCRIPT
    assert (
        SCRIPT.index('recorded="$(')
        < SCRIPT.index('helm upgrade --install "$SERVICE_NAME"')
        < SCRIPT.index("if ! checks")
        < SCRIPT.index('helm rollback "$SERVICE_NAME"')
    )
    assert "no earlier deployed revision" in SCRIPT
    assert 'case "$DEPLOY_ENV" in dev|qa|production' in SCRIPT


def test_no_port_or_api_path_is_hard_coded() -> None:
    for text in (SCRIPT, (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")):
        assert "18000" not in text
        assert "/api/v1" not in text
        assert ":8000" not in text
    env = _dep()["env"]
    assert {"BACKEND_PORT", "FRONTEND_PORT", "API_VERSION", "DEPLOY_IMAGES"} <= set(env)


def test_both_workflows_run_bash_with_pipefail() -> None:
    for doc in (_dep(), _ops()):
        assert doc["defaults"] == {"run": {"shell": "bash"}}


def test_a_smoke_report_upload_cannot_trigger_a_rollback() -> None:
    for name in DEPLOY_JOBS:
        assert "helm rollback" not in str(_jobs()[name])


def test_the_release_workflow_calls_deploy_with_the_plan_and_digests() -> None:
    job = workflow("release.yml")["jobs"]["deploy"]
    assert job["uses"] == "./.github/workflows/deploy.yml"
    assert {"plan", "digests", "images"} <= set(job["needs"])
    assert not {"grant", "vex"} & set(job["needs"])
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["with"]["version"] == "${{ needs.plan.outputs.version }}"
    assert job["with"]["image_tag"] == "${{ needs.plan.outputs.image_tag }}"
    assert job["with"]["immutable"] == "${{ needs.plan.outputs.immutable }}"
    assert job["with"]["digests"] == "${{ needs.digests.outputs.digests }}"
    assert "digests" in workflow("release.yml")["jobs"]["digests"]["outputs"]
    assert "workflow_run" not in str(_dep()["on"])


def test_ops_inputs_and_environment() -> None:
    inputs = _ops()["on"]["workflow_dispatch"]["inputs"]
    assert inputs["environment"]["options"] == ["dev", "qa", "production"]
    assert inputs["operation"]["options"] == ["rollback", "history", "cleanup", "check"]
    assert {"revision", "confirm"} <= set(inputs)
    job = _ops()["jobs"]["ops"]
    assert job["environment"]["name"] == "${{ inputs.environment }}"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert _uses_cluster_access(job)


def _step(name_part: str) -> dict[str, Any]:
    return next(s for s in _ops()["jobs"]["ops"]["steps"] if name_part in s.get("name", "").lower())


def test_rollback_needs_the_confirmation_in_the_if_and_in_a_validating_step() -> None:
    steps = _ops()["jobs"]["ops"]["steps"]
    rollbacks = [s for s in steps if "helm rollback" in s.get("run", "")]
    assert len(rollbacks) == 1
    cond = rollbacks[0]["if"]
    assert "inputs.operation == 'rollback'" in cond
    assert "inputs.confirm == 'ROLLBACK'" in cond
    check = next(s for s in steps if s.get("env", {}).get("CONFIRM") == "${{ inputs.confirm }}")
    assert '"ROLLBACK"' in check["run"]
    assert "exit 1" in check["run"]
    rev = next(s for s in steps if s.get("env", {}).get("REVISION") == "${{ inputs.revision }}")
    assert "^[0-9]+$" in rev["run"]
    assert steps.index(check) < steps.index(rollbacks[0])
    assert steps.index(rev) < steps.index(rollbacks[0])


def test_history_runs_helm_history_and_cleanup_deletes_nothing() -> None:
    history = _step("history")
    assert "helm history" in history["run"]
    assert "inputs.operation == 'history'" in history["if"]
    cleanup = _step("cleanup")
    assert "inputs.operation == 'cleanup'" in cleanup["if"]
    for word in ("delete", "uninstall", "rollback", "rm ", "prune", "patch", "apply"):
        assert word not in cleanup["run"], word


def test_every_deploy_and_ops_job_installs_helm_and_kubectl_from_the_pins() -> None:
    jobs = [_jobs()[n] for n in DEPLOY_JOBS] + [_ops()["jobs"]["ops"]]
    for job in jobs:
        install = next(s for s in job["steps"] if "scripts/tools.sh install" in s.get("run", ""))
        assert "install helm4 kubectl" in install["run"]
        assert job["steps"].index(install) < next(
            i for i, s in enumerate(job["steps"]) if s.get("uses") == COMPOSITE
        )
    pins = {
        ln.split("\t")[0]: ln.split("\t")
        for ln in (REPO / "tools/pins.tsv").read_text().splitlines()
        if ln and ln[0] != "#"
    }
    assert pins["kubectl"][3].startswith("https://dl.k8s.io/release/v")
    assert len(pins["kubectl"][4]) == SHA256_HEX_LENGTH


def test_ops_shares_the_deploy_lock_of_its_environment() -> None:
    job = _ops()["jobs"]["ops"]
    assert job["concurrency"] == {
        "group": "deploy-${{ inputs.environment }}",
        "cancel-in-progress": False,
    }
    assert "github.ref" not in job["concurrency"]["group"]
    for name in DEPLOY_JOBS:
        assert _jobs()[name]["concurrency"]["group"] == f"deploy-{name.removeprefix('deploy-')}"


def test_ops_checks_the_environment_allowlist_through_env() -> None:
    steps = _ops()["jobs"]["ops"]["steps"]
    guard = next(s for s in steps if "dev|qa|production" in s.get("run", ""))
    assert steps.index(guard) < next(i for i, s in enumerate(steps) if "helm" in s.get("run", ""))
    assert "${{" not in guard["run"]
    assert _ops()["jobs"]["ops"]["env"]["DEPLOY_ENV"] == "${{ inputs.environment }}"


def test_check_operation_is_read_only_runs_through_cluster_access_and_needs_no_confirm() -> None:
    assert "check" in _ops()["on"]["workflow_dispatch"]["inputs"]["operation"]["options"]
    steps = _ops()["jobs"]["ops"]["steps"]
    check = next(s for s in steps if "inputs.operation == 'check'" in str(s.get("if", "")))
    assert "confirm" not in str(check["if"])
    assert [ln.strip() for ln in check["run"].strip().splitlines()] == [
        "kubectl version",
        'kubectl auth can-i --list -n "${SERVICE_NAME}-${DEPLOY_ENV}"',
    ]
    for word in ("helm upgrade", "helm rollback", "helm install"):
        assert word not in check["run"], word
    access = next(i for i, s in enumerate(steps) if s.get("uses") == COMPOSITE)
    guard = next(i for i, s in enumerate(steps) if "dev|qa|production" in s.get("run", ""))
    assert guard < access < steps.index(check)
    assert "if" not in steps[access]
    # the only step that needs the confirmation is the rollback validation
    needing = [
        s
        for s in steps
        if "inputs.confirm" in str(s.get("if", "")) or "CONFIRM" in s.get("env", {})
    ]
    assert all("rollback" in str(s.get("if", "")) for s in needing)
    # no step other than the rollback ever calls helm upgrade or rollback
    for s in steps:
        if "helm upgrade" in s.get("run", "") or "helm rollback" in s.get("run", ""):
            assert "inputs.operation == 'rollback'" in s["if"]


def test_only_a_rollback_creates_a_deployment_record() -> None:
    env = _ops()["jobs"]["ops"]["environment"]
    assert env["name"] == "${{ inputs.environment }}"
    assert env["deployment"] == "${{ inputs.operation == 'rollback' }}"
