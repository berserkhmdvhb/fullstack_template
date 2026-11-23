"""The kind job: called from ci.yml on pull requests, hardened, one leg per pinned node image, and
running the same scripts as `make kind-up kind-deploy`; CI OK lets it be skipped but never fail."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, cast

import pytest

from tests.supply_chain._workflows import REPO, workflow

BASH: str = shutil.which("bash") or "/usr/bin/bash"


def _ci() -> dict[str, Any]:
    return cast("dict[str, Any]", workflow("ci.yml")["jobs"])


def _kind() -> dict[str, Any]:
    return cast("dict[str, Any]", workflow("reusable-kind.yml")["jobs"]["kind"])


def _runs() -> str:
    return "\n".join(s.get("run", "") for s in _kind()["steps"])


def _legs() -> list[str]:
    rows = (REPO / "tools" / "kind-nodes.tsv").read_text(encoding="utf-8").splitlines()
    return [r.split("\t")[0] for r in rows if r and not r.startswith("#")]


def test_ci_calls_it_on_pull_requests_behind_its_path_filter_with_the_base_commit() -> None:
    job = _ci()["kind"]
    assert job["uses"] == "./.github/workflows/reusable-kind.yml"
    assert (
        job["if"] == "github.event_name == 'pull_request' && needs.changes.outputs.kind == 'true'"
    )
    assert job["needs"] == "changes"
    assert job["permissions"] == {"contents": "read"}
    assert job["with"] == {"base-sha": "${{ github.event.pull_request.base.sha }}"}
    assert _ci()["changes"]["outputs"]["kind"] == "${{ steps.filter.outputs.kind }}"


def test_the_path_filter_covers_the_chart_the_bootstrap_and_the_images() -> None:
    step = next(s for s in _ci()["changes"]["steps"] if s.get("id") == "filter")
    pattern = re.search(
        r"grep -Eq '([^']+)' <<<\"\$changed\"; then\n\s+echo \"kind=true\"", step["run"]
    )
    assert pattern
    deployed = ("charts/x", "infra/k8s/bootstrap/x", "scripts/kind-deploy.sh", "Dockerfile.waf")
    for path in (*deployed, "src/x.py", "frontend/x", "tools/kind-nodes.tsv", ".github/ci.yml"):
        assert re.search(pattern[1], path), path
    assert not re.search(pattern[1], "wiki/infrastructure.md")


def test_the_job_is_hardened() -> None:
    doc = workflow("reusable-kind.yml")
    assert doc["permissions"] == {}
    assert list(doc["on"]) == ["workflow_call"]
    job = _kind()
    assert job["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] <= 60  # noqa: PLR2004
    assert job["concurrency"]["cancel-in-progress"] is True
    text = json.dumps(job)
    for word in (
        "secrets.",
        "GITHUB_TOKEN",
        "docker login",
        "--push",
        "cache-from",
        "actions/cache",
    ):
        assert word not in text, word
    for step in job["steps"]:
        assert _hardened_step(step), step.get("name", step.get("uses"))


def _hardened_step(step: dict[str, Any]) -> bool:
    """A SHA-pinned action, a checkout that keeps no token, and no expression inside a script."""
    if "uses" in step:
        if not re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]):
            return False
        # persist-credentials is a checkout input: every checkout must set it to false.
        if step["uses"].startswith("actions/checkout@") and (
            step.get("with", {}).get("persist-credentials") is not False
        ):
            return False
    return "${{" not in step.get("run", "")


def test_a_checkout_that_keeps_its_token_is_not_hardened() -> None:
    checkout = next(
        s for s in _kind()["steps"] if s.get("uses", "").startswith("actions/checkout@")
    )
    assert _hardened_step(checkout)
    assert not _hardened_step({**checkout, "with": {"fetch-depth": 0}})
    assert not _hardened_step({**checkout, "with": {"persist-credentials": True}})
    assert not _hardened_step(
        {"uses": "actions/checkout@v7", "with": {"persist-credentials": False}}
    )
    upload = next(
        s for s in _kind()["steps"] if s.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert _hardened_step(upload)
    assert "persist-credentials" not in upload["with"]


def test_one_leg_per_pinned_node_image() -> None:
    assert _kind()["strategy"]["matrix"]["leg"] == _legs()
    assert _kind()["strategy"]["fail-fast"] is False


def test_it_runs_the_scripts_make_runs_and_always_deletes_the_cluster() -> None:
    runs = _runs()
    assert 'scripts/kind-up.sh --node "${LEG}"' in runs
    assert 'scripts/kind-deploy.sh --base "${BASE_SHA}"' in runs
    last = _kind()["steps"][-1]
    assert last["if"] == "always()"
    assert last["run"] == "scripts/kind-up.sh --delete"
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "scripts/kind-up.sh --node $(KIND_NODE)" in makefile
    assert "scripts/kind-deploy.sh $(if $(KIND_BASE),--base $(KIND_BASE))" in makefile
    assert "kind-down:\n\tscripts/kind-up.sh --delete" in makefile


def test_it_installs_every_kubectl_the_legs_name_through_the_pins() -> None:
    rows = (REPO / "tools" / "kind-nodes.tsv").read_text(encoding="utf-8").splitlines()
    pins = {r.split("\t")[1] for r in rows if r and not r.startswith("#")}
    install = next(ln for ln in _runs().splitlines() if "scripts/tools.sh install" in ln)
    for tool in (*pins, "kind", "ct", "helm4", "yq", "crd-sources"):
        assert f" {tool}" in install, tool


def test_the_build_is_the_images_jobs() -> None:
    script = (REPO / "scripts" / "kind-deploy.sh").read_text(encoding="utf-8")
    assert "bash scripts/ci/build-image.sh" in script
    assert "container_matrix.py --matrix" in script
    assert "kind load docker-image --name" in script


def _gate(results: dict[str, str], event: str) -> int:
    script = _ci()["ci-ok"]["steps"][-1]["run"]
    needs = json.dumps({k: {"result": v, "outputs": {}} for k, v in results.items()})
    env = {"NEEDS": needs, "EVENT_NAME": event, "PATH": os.environ["PATH"]}
    return subprocess.run(
        [BASH, "-c", script], env=env, capture_output=True, check=False
    ).returncode


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is needed to run the gate script")
@pytest.mark.parametrize("event", ["pull_request", "push"])
def test_ci_ok_lets_it_be_skipped_but_never_fail(event: str) -> None:
    assert "kind" in _ci()["ci-ok"]["needs"]
    jobs = dict.fromkeys(set(_ci()) - {"ci-ok"}, "success")
    assert _gate(jobs | {"kind": "skipped"}, event) == 0
    assert _gate(jobs | {"kind": "failure"}, event) != 0
    assert _gate(jobs | {"kind": "cancelled"}, event) != 0


def test_the_diagnostics_use_the_legs_own_kubectl() -> None:
    step = next(s for s in _kind()["steps"] if s.get("name") == "Show the cluster state")
    assert step["if"] == "failure()"
    assert "tools/kind-nodes.tsv" in step["run"]
    assert not re.search(r"^\s*kubectl\b", step["run"], re.MULTILINE), (
        "a bare kubectl skips the leg's pin"
    )
    assert '"${kubectl_pin}" --kubeconfig .local/kind/kubeconfig' in step["run"]


def _step(name: str) -> dict[str, Any]:
    return next(s for s in _kind()["steps"] if s.get("name") == name)


def _names() -> list[str]:
    return [s.get("name", s.get("uses", "")) for s in _kind()["steps"]]


SUITES = {
    "Live suites (backend and public forwards)": ("make kind-live", None),
    "Smoke test (public forward)": ("make kind-smoke", None),
    "Frontend live suite (Playwright, public forward)": ("make kind-e2e", "matrix.leg == 'newest'"),
    "CSP reports (a browser violation reaches the backend's log, public forward)": (
        "make kind-csp-report-test",
        "matrix.leg == 'newest'",
    ),
}
ZAP = "ZAP baseline (advisory, public forward)"
SHUTDOWN = next(n for n in _names() if n.startswith("Graceful shutdown"))


def test_the_suites_run_against_the_release_blocking_before_the_shutdown_check() -> None:
    names = _names()
    deploy = next(i for i, n in enumerate(names) if n.startswith("Deploy and test"))
    for name, (run, cond) in SUITES.items():
        step = _step(name)
        assert step["run"] == run
        assert step.get("if") == cond, name
        assert "continue-on-error" not in step, name
        assert deploy < names.index(name) < names.index(SHUTDOWN), name
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    for target in ("kind-live", "kind-smoke", "kind-e2e", "kind-zap"):
        recipe = makefile.split(f"\n{target}:\n", 1)[1].split("\n\n", 1)[0]
        assert "scripts/ci/kind-forward.sh" in recipe, target


def test_playwright_is_installed_as_the_live_workflow_installs_it() -> None:
    step = _step("Install the Playwright browser")
    live = workflow("reusable-live.yml")["jobs"]["live"]["steps"]
    theirs = next(s for s in live if s.get("name") == "Install the Playwright browser")
    assert step["run"] == theirs["run"]
    assert step["working-directory"] == theirs["working-directory"] == "frontend"
    assert "node" in next(ln for ln in _runs().splitlines() if "scripts/tools.sh install" in ln)


def test_zap_is_advisory_last_and_always_reports() -> None:
    names = _names()
    zap = _step(ZAP)
    assert zap["id"] == "zap"
    assert zap["continue-on-error"] is True
    assert str(zap["timeout-minutes"]) == "15"
    assert zap["if"] == "matrix.leg == 'newest'"
    assert zap["run"] == "make kind-zap"
    assert names.index(SHUTDOWN) < names.index(ZAP)
    after = _kind()["steps"][names.index(ZAP) + 1 :]
    reported = "${{ !cancelled() && steps.zap.outcome != 'skipped' }}"
    verdict = _step("ZAP verdict (advisory)")
    assert verdict["if"] == reported
    assert "exit 1" not in verdict["run"]
    assert "::warning" in verdict["run"]
    upload = _step("Upload the ZAP report")
    assert upload["if"] == reported
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["path"] == "output/security/dast/"
    # Nothing blocking runs after ZAP: only its report, the failure dump and the cluster delete.
    assert [s.get("name") for s in after] == [
        "ZAP verdict (advisory)",
        "Upload the ZAP report",
        "Show the cluster state",
        "Delete the cluster",
    ]
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\nkind-zap:\n", 1)[1].split("\n\n", 1)[0]
    assert "DAST_TARGET_RUNNING=true" in recipe
    assert 'DAST_ZAP_CONTAINER="fst-zap-' in recipe
    assert "infra/security/local/scan-dast.sh" in recipe


def test_the_failure_dump_prints_the_forwards_logs() -> None:
    assert '"${RUNNER_TEMP}"/kind-forward/*.log' in _step("Show the cluster state")["run"]


def test_the_migration_lifecycle_runs_on_the_floor_leg_after_the_shutdown_check() -> None:
    """Blocking, on the floor leg, after the steps that check the main release (it has its own)."""
    names = _names()
    name = next(n for n in names if n.startswith("Migration lifecycle"))
    step = _step(name)
    assert step["run"] == "make kind-migration-test"
    assert step["if"] == "matrix.leg == 'floor'"
    assert "continue-on-error" not in step
    assert names.index(SHUTDOWN) < names.index(name) < names.index(ZAP)
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\nkind-migration-test:\n", 1)[1].split("\n\n", 1)[0]
    assert "tests/k8s/test_migrations.py" in recipe
    shutdown = makefile.split("\nkind-shutdown-test:\n", 1)[1].split("\n\n", 1)[0]
    assert "tests/k8s/test_graceful_shutdown.py" in shutdown  # not the whole directory


def test_the_admission_test_runs_on_the_newest_leg_after_the_shutdown_check() -> None:
    """Blocking, after the steps that check the main release, before ZAP; its own make target."""
    names = _names()
    name = next(n for n in names if n.startswith("Image admission"))
    step = _step(name)
    assert step["run"] == "make kind-admission-test"
    assert step["if"] == "matrix.leg == 'newest'"
    assert "continue-on-error" not in step
    assert "timeout-minutes" in step  # its own, inside the job's
    assert names.index(SHUTDOWN) < names.index(name) < names.index(ZAP)
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("\nkind-admission-test:\n", 1)[1].split("\n\n", 1)[0]
    assert "tests/k8s/test_admission.py" in recipe
    dump = _step("Show the cluster state")["run"]
    assert "deploy/kyverno-admission-controller" in dump
    assert "imagevalidatingpolicies" in dump
    install = _step("Install the pinned tools")["run"]
    assert " cosign " in install
    assert "kyverno-chart" in install


def test_the_admission_tests_signing_has_no_identity_login_or_secret() -> None:
    """The test-only exception to the image-ops rule (CONVENTIONS.md, 5): the step signs with a key
    the job generates, so it needs no OIDC token, no registry login and no secret."""
    job = _kind()
    assert "id-token" not in job["permissions"]
    step = _step(next(n for n in _names() if n.startswith("Image admission")))
    assert set(step) <= {"name", "if", "timeout-minutes", "run"}, step  # no env, no action
    test = (REPO / "tests" / "k8s" / "test_admission.py").read_text(encoding="utf-8")
    for word in (
        "docker login",
        '"login"',
        "id-token",
        "ACTIONS_ID_TOKEN",
        "${{",  # no workflow expression (a secret, a token)
        "GITHUB_TOKEN",
    ):
        assert word not in test, word
    assert "127.0.0.1" in test  # pushes and signs through the loopback only
    rules = (REPO / "CONVENTIONS.md").read_text(encoding="utf-8")
    assert "wiki/admission-control.md#proven-on-kind" in rules


def _recipe(target: str) -> str:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    return makefile.split(f"\n{target}:\n", 1)[1].split("\n\n", 1)[0]


def test_the_csp_report_test_runs_after_playwright_is_installed_with_its_own_timeout() -> None:
    names = _names()
    name = next(n for n in names if n.startswith("CSP reports"))
    assert names.index("Install the Playwright browser") < names.index(name)
    assert "timeout-minutes" in _step(name)
    assert "tests/k8s/test_csp_report.py" in _recipe("kind-csp-report-test")
    script = (REPO / "scripts" / "kind-deploy.sh").read_text(encoding="utf-8")
    assert 'deploy "$CHART" --set waf.enabled=true --set cspReporting.enabled=true' in script


def test_the_tracing_and_redis_test_runs_on_the_newest_leg_after_the_shutdown_check() -> None:
    """Blocking, after the steps that check the main release, before ZAP; its own make target, its
    own timeout, and its collector's and Redis's logs in the failure dump."""
    names = _names()
    name = next(n for n in names if n.startswith("Tracing and Redis"))
    step = _step(name)
    assert step["run"] == "make kind-tracing-redis-test"
    assert step["if"] == "matrix.leg == 'newest'"
    assert "continue-on-error" not in step
    assert "timeout-minutes" in step
    assert set(step) <= {"name", "if", "timeout-minutes", "run"}, step  # no env, no secret
    assert names.index(SHUTDOWN) < names.index(name) < names.index(ZAP)
    assert "tests/k8s/test_tracing_redis.py" in _recipe("kind-tracing-redis-test")
    dump = _step("Show the cluster state")["run"]
    assert "fst-otel-collector fst-redis" in dump
    assert "-n your-service-name-feat-local" in dump
