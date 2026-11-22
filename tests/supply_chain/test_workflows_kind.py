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
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
            assert step["with"]["persist-credentials"] is False
        assert "${{" not in step.get("run", ""), step.get("name")


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
