# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Every Pod that mounts the backup volume (PostgreSQL, the backup and the verify jobs) has the
same fsGroup with fsGroupChangePolicy OnRootMismatch, so the kubelet never flips the volume's group
between them and the backup jobs, whatever their uid, can write it."""

from __future__ import annotations

import os

import pytest

from tests.backend.test_chart_pod_security import VALUES_FILES, pod_specs, render
from tests.backend.test_chart_production_guard import CHART, find_helm, require_helm

CLAIM = "svc-your-service-name-pg-backup-pvc"


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


@pytest.mark.parametrize("values", [v for v in VALUES_FILES if v != "values.production.yaml"])
def test_every_pod_mounting_the_backup_volume_has_one_fs_group(helm: str, values: str) -> None:
    mounting = {
        name: spec["securityContext"]
        for name, (spec, _) in pod_specs(render(helm, values)).items()
        if any(
            (v.get("persistentVolumeClaim") or {}).get("claimName") == CLAIM
            for v in spec.get("volumes") or []
        )
    }
    assert set(mounting) == {
        "StatefulSet/svc-your-service-name-postgresql",
        "CronJob/svc-your-service-name-pg-backup",
        "CronJob/svc-your-service-name-pg-backup-verify",
    }
    assert {ctx.get("fsGroup") for ctx in mounting.values()} == {999}
    assert {ctx.get("fsGroupChangePolicy") for ctx in mounting.values()} == {"OnRootMismatch"}
    # Each Pod keeps its own uid: the jobs are not PostgreSQL.
    assert mounting["CronJob/svc-your-service-name-pg-backup"]["runAsUser"] == 10001  # noqa: PLR2004
    assert mounting["StatefulSet/svc-your-service-name-postgresql"]["runAsUser"] == 999  # noqa: PLR2004


def test_the_verify_config_map_holds_the_script_byte_for_byte(helm: str) -> None:
    """The restore check runs the tested script (tests/scripts/pg-backup-verify.bats), unchanged."""
    (cm,) = [
        d
        for d in render(helm, "values.yaml")
        if d["kind"] == "ConfigMap"
        and d["metadata"]["name"] == "svc-your-service-name-pg-backup-verify"
    ]
    assert (
        cm["data"]["pg-backup-verify.sh"] == (CHART / "files" / "pg-backup-verify.sh").read_text()
    )
