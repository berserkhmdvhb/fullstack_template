"""Production in its own project: each job reading the cluster variables runs in an environment."""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.supply_chain._workflows import REPO, WORKFLOWS, load

CLUSTER_VARS = re.compile(r"vars\.(GCP_PROJECT_ID|GKE_CLUSTER|GKE_LOCATION)\b")
PLATFORM = (REPO / "wiki" / "platform-setup.md").read_text(encoding="utf-8")
GKE = (REPO / "wiki" / "gke-cluster-setup.md").read_text(encoding="utf-8")


def _readers() -> list[tuple[str, str, dict[str, Any]]]:
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, job in (load(path).get("jobs") or {}).items():
            if CLUSTER_VARS.search(str(job)):
                found.append((path.name, name, job))
    return found


def test_the_deploy_workflows_are_covered() -> None:
    assert {"deploy.yml", "deploy-ops.yml"} <= {f for f, _, _ in _readers()}


@pytest.mark.parametrize(
    ("file", "name", "job"), _readers(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_a_job_reading_the_cluster_variables_declares_an_environment(
    file: str, name: str, job: dict[str, Any]
) -> None:
    # An environment variable overrides the repository one only for a job with `environment:`
    # (https://docs.github.com/en/actions/learn-github-actions/variables, "Precedence").
    assert job.get("environment"), (
        f"{file}: job {name} reads the cluster variables without `environment:`"
    )


def test_the_docs_explain_production_in_its_own_project() -> None:
    for name in (
        "GCP_PROJECT_ID",
        "GKE_CLUSTER",
        "GKE_LOCATION",
        "GCP_WIF_PROVIDER",
        "GCP_DEPLOYER_SA",
    ):
        assert f"ENV_production_{name}=" in PLATFORM
    assert "platform-setup.md#production-in-its-own-project" in GKE
