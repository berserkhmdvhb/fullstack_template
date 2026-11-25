# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
A live release switched from the chart's own Secret to the External Secrets Operator, on the kind
cluster fst (wiki/changelog.md, Upgrading; wiki/gke-cluster-setup.md, External Secrets).

Run with ``make kind-external-secrets-test`` after ``make kind-up kind-deploy``; ``make test``
never collects it. The operator, OpenBao and the stores are test_external_secrets.py's, set up
and removed the same way. The release is first installed as local and kind run it, with its own
Secret (``secrets.dbPassword`` and ``statsPass``, as values.secret.yaml gives them, from a file
in a temporary directory), then upgraded with the store values only, OpenBao holding the values
the release already runs on, as the upgrade note says it must. The upgrade converges: both
ExternalSecrets are Ready, the app Secret and the database's password hold the same values as
before, the Pods and the migration Jobs are fine, and no revision is left failed or pending. No
value is printed.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.k8s._kube import _run
from tests.k8s.test_external_secrets import (
    NAMESPACE,
    RELEASE,
    STORE,
    Setup,
    _ready,
    environment,
)

# What the release held before the switch: its values (compared, never printed), and the owner of
# its Secret.
BEFORE: dict[str, Any] = {}
OWNER_BEFORE: list[Any] = []


def _chart_secret(s: Setup) -> None:
    """The release as local and kind deploy it: the chart's own Secret, with OpenBao's values."""
    values = {
        "secrets": {
            "dbUser": s.values["DB_USER"],
            "dbPassword": s.values["DB_PASSWORD"],
            "statsPass": s.values["STATS_PASS"],
        }
    }
    s.deploy(values, "the chart's own Secret")
    BEFORE.update(
        app=s.secret_data(s.app, f"{RELEASE}-secret"),
        db=s.secret_data(s.db, f"{RELEASE}-db-credentials").get("password"),
    )
    OWNER_BEFORE.extend(
        s.app.get("secret", f"{RELEASE}-secret")["metadata"].get("ownerReferences", [])
    )


@pytest.fixture(scope="module")
def switched(request: pytest.FixtureRequest) -> Iterator[Setup]:
    with environment(request, before=_chart_secret) as s:
        yield s


def test_before_the_switch_the_release_ran_on_the_charts_own_secret(switched: Setup) -> None:
    assert OWNER_BEFORE == [], OWNER_BEFORE  # Helm's, not an ExternalSecret's
    same = BEFORE["app"] == switched.values and BEFORE["db"] == switched.values["DB_PASSWORD"]
    assert same, "the chart's Secret did not hold the values given"


def test_the_upgrade_left_no_revision_failed_or_pending(switched: Setup) -> None:
    history = _run([*switched.helm, "history", RELEASE, "-n", NAMESPACE, "-o", "json"])
    statuses = [h["status"] for h in json.loads(history)]
    assert len(statuses) == 2, statuses  # noqa: PLR2004 - the install and the switch
    assert statuses == ["superseded", "deployed"], statuses


def test_both_external_secrets_are_ready_after_the_switch(switched: Setup) -> None:
    for kube, name in (
        (switched.app, f"{RELEASE}-secret"),
        (switched.db, f"{RELEASE}-db-password"),
    ):
        es = kube.get("externalsecret", name)
        assert es["spec"]["secretStoreRef"]["name"] == STORE, es["spec"]
        assert _ready(es) == ("True", "SecretSynced"), es.get("status")


def test_the_secret_keeps_its_values_and_is_now_the_external_secrets(switched: Setup) -> None:
    app = switched.secret_data(switched.app, f"{RELEASE}-secret")
    same = app == BEFORE["app"]
    assert same, "the app Secret's values changed in the switch"
    db = switched.secret_data(switched.db, f"{RELEASE}-db-password")
    same = db.get("password") == BEFORE["db"]
    assert same, "the database's password changed in the switch"
    owner = switched.app.get("secret", f"{RELEASE}-secret")["metadata"]["ownerReferences"][0]
    assert owner["kind"] == "ExternalSecret", owner


def test_the_pods_and_the_migration_jobs_are_fine_after_the_switch(switched: Setup) -> None:
    selector = f"app.kubernetes.io/instance={RELEASE}"
    for kube in (switched.app, switched.db):
        for pod in kube.get("pods", "-l", selector)["items"]:
            phase = pod["status"].get("phase")
            if phase == "Succeeded":
                continue  # a finished Job's Pod
            ready = {c["type"]: c["status"] for c in pod["status"].get("conditions", [])}
            name = pod["metadata"]["name"]
            assert phase == "Running", (name, pod["status"])
            assert ready.get("Ready") == "True", (name, pod["status"])
    jobs = switched.app.get("jobs", "-l", selector)["items"]
    assert jobs, "no migration Job"
    for job in jobs:
        assert job["status"].get("succeeded") == 1, job["status"]
        assert job["status"].get("failed", 0) == 0, job["status"]
