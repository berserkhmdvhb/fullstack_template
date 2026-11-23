# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The backup volume and the restore check, as rendered.

- Every Pod that mounts the backup volume (PostgreSQL, the backup and the verify jobs) has the same
  fsGroup with fsGroupChangePolicy OnRootMismatch, so the kubelet never flips the volume's group
  between them and the backup jobs, whatever their uid, can write it.
- The verify CronJob's ConfigMap holds the tested script byte for byte.
- Every table the restore check requires (postgresql.backup.verifyTables) is one the migrations
  create.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from tests.backend.test_chart_pod_security import VALUES_FILES, pod_specs, render
from tests.backend.test_chart_production_guard import CHART, find_helm, require_helm
from your_service_name.backend.db.run_migrations import run_migrations
from your_service_name.backend.settings import Settings

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


@pytest.mark.parametrize("values", [v for v in VALUES_FILES if v != "values.production.yaml"])
async def test_every_table_the_restore_check_requires_is_one_the_migrations_create(
    helm: str, values: str
) -> None:
    """VERIFY_TABLES, as the verify CronJob renders it, names only tables a dump of the migrated
    database holds: a table no migration creates fails every restore check."""
    (job,) = [
        d
        for d in render(helm, values)
        if d["kind"] == "CronJob"
        and d["metadata"]["name"] == "svc-your-service-name-pg-backup-verify"
    ]
    (container,) = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    env = {e["name"]: e.get("value") for e in container["env"]}
    await run_migrations(Settings())
    engine = create_async_engine(Settings().database_url)
    try:
        async with engine.connect() as conn:
            tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    finally:
        await engine.dispose()
    assert set(env["VERIFY_TABLES"].split(",")) <= tables, sorted(tables)
