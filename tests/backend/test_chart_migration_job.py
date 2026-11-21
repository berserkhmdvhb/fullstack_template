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


def _render(template: str, env: str = "dev", *extra: str, release: str = RELEASE) -> dict[str, Any]:
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
            f"templates/{template}",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    doc: dict[str, Any] = yaml.safe_load(out)
    return doc


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
    assert container["command"][-3:] == [
        "/app/.venv/bin/python",
        "-m",
        "your_service_name.backend.db.run_migrations",
    ]
    assert "sh" not in {c.rsplit("/", 1)[-1] for c in container["command"]}, (
        "the image has no shell"
    )
    env = {e["name"]: e["value"] for e in container["env"] if "value" in e}
    assert env["DATABASE_URL"].startswith("postgresql+asyncpg://$(DB_USER):$(DB_PASSWORD)@")
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


def test_the_job_is_kept_when_a_rollback_removes_the_revision() -> None:
    """helm.sh/resource-policy keep: Helm skips deleting it, so a failed Job's logs survive."""
    annotations = _job()["metadata"]["annotations"]
    assert annotations["helm.sh/resource-policy"] == "keep"


def test_a_release_named_like_the_chart_gives_the_documented_job_name() -> None:
    assert _job(release="your-service-name")["metadata"]["name"] == "your-service-name-migrate-r1"


def test_notes_use_the_same_name_helper_as_the_job() -> None:
    notes = (CHART / "templates" / "NOTES.txt").read_text(encoding="utf-8")
    job = (CHART / "templates" / "migration-job.yaml").read_text(encoding="utf-8")
    assert 'include "chart.migrationJobName"' in notes
    assert 'include "chart.migrationJobName"' in job


CA = (
    "--set",
    "database.tls.caSecret.name=db-ca",
    "--set",
    "database.tls.caSecret.key=server-ca.pem",
)
CA_PATH = "/etc/db-ca/ca.pem"


def _workloads(*extra: str) -> dict[str, dict[str, Any]]:
    dep = _render("backend-deployment.yaml", "dev", *extra)["spec"]["template"]["spec"]
    job = _render("migration-job.yaml", "dev", *extra)["spec"]["template"]["spec"]
    return {"backend": dep, "migration": job}


def _container(pod: dict[str, Any]) -> dict[str, Any]:
    container: dict[str, Any] = pod["containers"][0]
    return container


def test_a_database_ca_secret_is_mounted_read_only_in_both_workloads() -> None:
    for name, pod in _workloads(*CA).items():
        volume = next(v for v in pod["volumes"] if v["name"] == "db-ca")
        assert volume["secret"]["secretName"] == "db-ca", name
        assert volume["secret"]["items"] == [{"key": "server-ca.pem", "path": "ca.pem"}], name
        mount = next(m for m in _container(pod)["volumeMounts"] if m["name"] == "db-ca")
        assert mount["readOnly"] is True
        assert mount["mountPath"] == "/etc/db-ca"
        env = {e["name"]: e.get("value") for e in _container(pod)["env"]}
        assert env["DB_SSLROOTCERT"] == CA_PATH, name


def test_without_a_database_ca_secret_nothing_is_mounted() -> None:
    for name, pod in _workloads().items():
        assert "db-ca" not in str(pod.get("volumes")), name
        assert "db-ca" not in str(_container(pod).get("volumeMounts")), name
        assert "DB_SSLROOTCERT" not in str(_container(pod).get("env")), name


def test_production_keeps_verify_full_and_leaves_the_ca_secret_empty() -> None:
    import yaml as _y  # noqa: PLC0415

    values = _y.safe_load((CHART / "values.production.yaml").read_text(encoding="utf-8"))
    assert values["config"]["DB_SSLMODE"] == "verify-full"
    assert not values.get("database", {}).get("tls", {}).get("caSecret", {}).get("name")


PROXY = (
    "--set-json",
    'backend.extraInitContainers=[{"name":"proxy","image":"example.test/proxy@sha256:'
    + "0" * 64
    + '","restartPolicy":"Always"}]',
)


def test_extra_init_containers_come_first_in_both_workloads() -> None:
    for name, pod in _workloads(*PROXY).items():
        names = [c["name"] for c in pod["initContainers"]]
        assert names[0] == "proxy", name
    assert [c["name"] for c in _workloads(*PROXY)["migration"]["initContainers"]] == [
        "proxy",
        "wait-for-db",
    ]


def test_no_init_containers_by_default_on_the_backend() -> None:
    assert "initContainers" not in _workloads()["backend"]
