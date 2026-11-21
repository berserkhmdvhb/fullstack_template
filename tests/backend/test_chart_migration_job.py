# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The migration Job is a plain release resource, one per revision: not a Helm hook."""

from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest
import yaml

from tests.backend.test_chart_production_guard import CHART, find_helm, require_helm

RELEASE = "svc"
FULLNAME = "svc-your-service-name"


def _job(env: str = "dev", *extra: str, release: str = RELEASE) -> dict[str, Any]:
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    out = subprocess.run(
        [
            helm,
            "template",
            release,
            str(CHART),
            "-n",
            "team-ns",
            "-f",
            str(CHART / f"values.{env}.yaml"),
            "-s",
            "templates/migration-job.yaml",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    job: dict[str, Any] = yaml.safe_load(out)
    return job


@pytest.mark.parametrize("env", ["dev", "qa", "local", "production"])
def test_the_job_is_not_a_helm_hook(env: str) -> None:
    annotations = _job(env)["metadata"].get("annotations") or {}
    assert not [key for key in annotations if key.startswith("helm.sh/hook")]


def test_the_job_name_carries_the_release_revision() -> None:
    """A Job spec is immutable, so each revision needs its own Job."""
    assert _job()["metadata"]["name"] == f"{FULLNAME}-migrate-r1"
    template = (CHART / "templates" / "migration-job.yaml").read_text(encoding="utf-8")
    assert ".Release.Revision" in template


def test_the_job_is_cleaned_up_and_bounded() -> None:
    spec = _job()["spec"]
    assert spec["ttlSecondsAfterFinished"] == 86400  # noqa: PLR2004
    assert spec["backoffLimit"] == 3  # noqa: PLR2004
    assert spec["activeDeadlineSeconds"] == 300  # noqa: PLR2004


def test_the_job_runs_the_migration_module_with_the_backend_image() -> None:
    pod = _job()["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert "python -m your_service_name.backend.db.run_migrations" in " ".join(container["command"])
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert "backend" in container["image"]


def test_the_job_keeps_the_migration_component_label() -> None:
    """The network policies and the deploy verification select it by this label."""
    job = _job()
    assert job["metadata"]["labels"]["app.kubernetes.io/component"] == "migration"
    assert job["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/component"] == (
        "migration"
    )


def test_a_long_release_name_still_gives_a_valid_job_name() -> None:
    name = _job(release="r" * 50)["metadata"]["name"]
    assert len(name) <= 63  # noqa: PLR2004
    assert name.endswith("-migrate-r1")
