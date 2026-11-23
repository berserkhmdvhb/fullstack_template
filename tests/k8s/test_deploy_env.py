# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
scripts/ci/deploy-env.sh itself on the kind cluster fst (wiki/ci-cd.md, "Deploy chain").

Run with ``make kind-deploy-env-test`` after ``make kind-up kind-deploy``; ``make test`` never
collects it (tests/conftest.py ignores tests/k8s). It talks to fst only, through
.local/kind/kubeconfig, and refuses any other context; so does the script's test-only
``DEPLOY_ENV=kind`` (DEPLOY_ENV_KIND_TEST=1, the one context kind-fst). The kind job's images are
pushed to the throwaway TLS registry fst-registry (tests/k8s/_kube.py), so the script deploys them
by registry digest as release.yml's are. The script runs as deploy.yml runs it: helm and kubectl
as the bootstrap's deployer, the deploy workflow's default HELM_TIMEOUT, the placeholder guard on,
verify and smoke after the upgrade. Its own release in its own namespaces (bootstrapped as cluster
admin, removed at the end, the registry with them), so the kind job's main release is untouched.

One lifecycle, in file order (the make target stops at the first failure): a first install, a no-op
upgrade, a failed upgrade (Helm rolls it back), a failed verify (the script rolls back to the
recorded revision and fails), then a failed first install and a retry with no manual cleanup.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.k8s import _kube
from tests.k8s._kube import KUBECONFIG, ROOT, Kube, Pushed, _run, _tool, deploy_env

RELEASE = "your-service-name-dep"  # contains the chart's name, so the fullname is the release name
NAMESPACE = f"{RELEASE}-kind"  # the script's <SERVICE_NAME>-<DEPLOY_ENV>
DB_NAMESPACE = f"{NAMESPACE}-db"
JOB_PREFIX = f"{RELEASE}-migrate-r"
BACKEND = "app.kubernetes.io/component=backend"
DONE = f"Deployed, verified and smoke tested: {RELEASE} in kind"


class Deploy:
    """The script on this release, and what it left behind (as cluster admin, read only)."""

    def __init__(self, work: Path, pushed: Pushed) -> None:
        self.app = Kube(NAMESPACE)
        self.work, self.pushed = work, pushed
        _kube.deployer_kubectl(work / "bin", self.app.base[0])
        self.helm = [_tool("helm"), "--kubeconfig", str(KUBECONFIG)]
        self.helm += ["--kube-context", _kube.CONTEXT]

    def values(self, name: str, text: str) -> Path:
        path = self.work / f"{name}.yaml"
        path.write_text(text)
        return path

    def run(self, *values: Path, verify: Path | None = None, timeout: str = "") -> tuple[int, str]:
        started = time.time()
        rc, out = deploy_env(
            RELEASE, self.work, self.pushed, *values, verify=verify, timeout=timeout
        )
        print(f"deploy-env {[v.name for v in values]}: rc {rc}, {time.time() - started:.0f}s")
        print(out)
        return rc, out

    def history(self) -> list[dict[str, Any]]:
        out = _run([*self.helm, "history", RELEASE, "-n", NAMESPACE, "-o", "json"], check=False)
        return list(json.loads(out or "[]"))

    def head(self) -> tuple[int, str, str]:
        last = self.history()[-1]
        return last["revision"], last["status"], last["description"]

    def user_values(self) -> dict[str, Any]:
        out = _run([*self.helm, "get", "values", RELEASE, "-n", NAMESPACE, "-o", "json"])
        return dict(json.loads(out) or {})

    def backend_pods(self) -> dict[str, dict[str, Any]]:
        """The live backend Pods by uid."""
        pods = self.app.get("pods", "-l", BACKEND).get("items", [])
        return {p["metadata"]["uid"]: p for p in pods if not p["metadata"].get("deletionTimestamp")}


def _ready(pod: dict[str, Any]) -> bool:
    conditions = pod.get("status", {}).get("conditions", [])
    return any(c["type"] == "Ready" and c["status"] == "True" for c in conditions)


def _bootstrap() -> str:
    cmd = ["bash", str(ROOT / "scripts" / "k8s-bootstrap.sh"), "local"]
    return _run([*cmd, "--release", RELEASE, "--namespace", NAMESPACE])


def _remove(admin: list[str], helm: list[str], objects: str) -> None:
    """The release, its kept Jobs and the bootstrap's objects (the namespaces with them)."""
    if _run([*helm, "status", RELEASE, "-n", NAMESPACE], check=False):
        _run([*helm, "uninstall", RELEASE, "-n", NAMESPACE, "--wait"], check=False)
    _run([*admin, "delete", "-f", "-", "--ignore-not-found", "--wait"], objects, check=False)


@pytest.fixture(scope="module")
def deploy() -> Iterator[Deploy]:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    work = Path(tempfile.mkdtemp(prefix="fst-deploy-env-"))
    try:
        main = Kube()
        admin = main.base[:-2]  # without -n, for the bootstrap's objects
        helm = [_tool("helm"), "--kubeconfig", str(KUBECONFIG), "--kube-context", _kube.CONTEXT]
        objects = _bootstrap()
        _remove(admin, helm, objects)  # what an interrupted run left behind
        tls = _kube.registry_tls(work / "tls")
        with _kube.registry(main, tls) as host:
            pushed = _kube.push_release_images(host, tls, "fst-deploy")
            _run([*admin, "apply", "-f", "-"], objects)
            try:
                yield Deploy(work, pushed)
            finally:
                _remove(admin, helm, objects)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---- The lifecycle, in order ---------------------------------------------------------------------


def test_a_first_install_deploys_by_digest_verifies_and_smoke_tests(deploy: Deploy) -> None:
    rc, out = deploy.run()
    assert rc == 0, out
    assert "last deployed revision before this deploy: none (first install)" in out
    assert "waf: not rendered by this release (feature off) and no WAF_IMAGE_DIGEST" in out
    assert "== Verification complete: all checks passed ==" in out
    assert DONE in out
    assert deploy.head()[:2] == (1, "deployed")
    want = deploy.pushed.digests["BACKEND_IMAGE_DIGEST"]
    for pod in deploy.backend_pods().values():
        assert pod["spec"]["containers"][0]["image"].endswith(f"@{want}")
        assert pod["status"]["containerStatuses"][0]["imageID"].endswith(f"@{want}")
    report = (deploy.work / "run" / "smoke-kind.xml").read_text()
    assert 'failures="0"' in report, report


def test_a_no_op_upgrade_records_revision_1_and_deploys_revision_2(deploy: Deploy) -> None:
    rc, out = deploy.run()
    assert rc == 0, out
    assert "last deployed revision before this deploy: 1" in out
    assert DONE in out
    assert deploy.head()[:2] == (2, "deployed")
    assert "back to revision" not in out


def test_a_failed_upgrade_is_rolled_back_and_the_old_revision_keeps_serving(
    deploy: Deploy,
) -> None:
    """The migration fails on purpose (tests.migration.fail, the Job's only change): Helm fails the
    upgrade and rolls it back (--rollback-on-failure), the script prints the failed Job's logs and
    fails before verify, and revision 2's Pods, never replaced, keep serving."""
    before = deploy.backend_pods()
    fail = deploy.values("fail", "tests: {migration: {fail: true}}\n")
    rc, out = deploy.run(fail)
    assert rc != 0
    assert f"migration Job of {RELEASE} in kind (the deploy failed)" in out
    assert f"migration job: {JOB_PREFIX}3|" in out
    assert "MIGRATION_TEST_FAIL: failing on purpose" in out
    assert "== Check 1" not in out  # verify never ran
    history = deploy.history()
    assert [(h["revision"], h["status"]) for h in history[-2:]] == [(3, "failed"), (4, "deployed")]
    assert "Rollback to 2" in history[-1]["description"]
    assert "migration" not in deploy.user_values()["tests"]  # revision 2's values
    after = deploy.backend_pods()
    assert set(after) == set(before), "the failed upgrade replaced the backend Pods"
    assert all(_ready(p) for p in after.values())


def test_a_failed_verify_rolls_back_to_the_recorded_revision_and_fails(deploy: Deploy) -> None:
    """The upgrade succeeds (revision 5), then the real deploy-verify.sh fails: it is told to expect
    the frontend's digest on the backend Pods (a wrong image). The script rolls back to the
    revision it recorded before the upgrade (4, the last deployed), skips the smoke, and fails."""
    real = ROOT / "scripts" / "ci" / "deploy-verify.sh"
    verify = deploy.work / "verify-wrong-image.sh"
    verify.write_text(
        '#!/usr/bin/env bash\nBACKEND_IMAGE_DIGEST="$FRONTEND_IMAGE_DIGEST" '
        f'exec bash "{real}" "$@"\n'
    )
    smoke = deploy.work / "run" / "smoke-kind.xml"
    smoke.unlink(missing_ok=True)
    roll = deploy.values("roll", "config:\n  LOG_LEVEL: INFO\n")
    rc, out = deploy.run(roll, verify=verify)
    assert rc == 1, out
    assert "last deployed revision before this deploy: 4" in out
    assert "digest mismatch" in out
    assert f"verify or smoke failed; rolled {RELEASE} back to revision 4 in kind" in out
    assert not smoke.exists(), "the smoke test ran after a failed verify"
    history = deploy.history()
    assert [(h["revision"], h["status"]) for h in history[-2:]] == [
        (5, "superseded"),
        (6, "deployed"),
    ]
    assert "Rollback to 4" in history[-1]["description"]
    assert deploy.user_values()["config"]["LOG_LEVEL"] == "DEBUG"  # values.local.yaml's, not INFO
    assert all(_ready(p) for p in deploy.backend_pods().values())


def test_a_failed_first_install_then_a_retry_succeeds_with_no_manual_cleanup(
    deploy: Deploy,
) -> None:
    """The release uninstalled (the database kept): the first install's migration fails on purpose,
    --rollback-on-failure uninstalls it and keeps the Failed Job, whose logs the script prints. The
    same deploy without the failure deletes that Job itself and installs revision 1."""
    _run([*deploy.helm, "uninstall", RELEASE, "-n", NAMESPACE, "--wait"])
    deploy.app("delete", "jobs", "-l", f"app.kubernetes.io/instance={RELEASE}", "--wait")

    fail = deploy.values("fail-first", "tests:\n  migration:\n    fail: true\n")
    rc, out = deploy.run(fail)
    assert rc != 0
    assert "last deployed revision before this deploy: none (first install)" in out
    assert "MIGRATION_TEST_FAIL: failing on purpose" in out
    assert not deploy.history(), "the failed first install was not uninstalled"
    failed = deploy.app.get("job", f"{JOB_PREFIX}1")
    assert any(
        c["type"] == "Failed" and c["status"] == "True" for c in failed["status"]["conditions"]
    )

    rc, out = deploy.run()
    assert rc == 0, out
    assert f"deleting the Failed migration Job {JOB_PREFIX}1 of {RELEASE}" in out
    assert DONE in out
    assert deploy.head()[:2] == (1, "deployed")
    assert deploy.app.get("job", f"{JOB_PREFIX}1")["metadata"]["uid"] != failed["metadata"]["uid"]
