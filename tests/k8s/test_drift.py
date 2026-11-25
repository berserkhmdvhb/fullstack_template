# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The live-drift check on the kind cluster fst (wiki/ci-cd.md, "Live drift").

Run with ``make kind-drift-test`` after ``make kind-up kind-deploy``; ``make test`` never collects
it (tests/conftest.py ignores tests/k8s). It talks to fst only, through .local/kind/kubeconfig. A
release and namespaces of its own (bootstrapped as cluster admin with the bootstrap's drift Role
bound to a ServiceAccount, removed at the end, the registry with them): scripts/ci/deploy-env.sh
deploys it as the deploy workflow does (test-only DEPLOY_ENV=kind, the kind job's images by
digest), the same script renders it (DEPLOY_RENDER_ONLY), and scripts/drift.py compares the two
with nothing but the drift identity: a token for its ServiceAccount (TokenRequest, never printed)
in a kubeconfig of its own.

One lifecycle, in file order (the make target stops at the first failure): a clean deploy reports
nothing; each of six hand edits is reported, then a redeploy takes the field back (the deploy
forces the conflict with kubectl's field manager; the extra object is removed by hand: Helm does
not own it) and nothing is reported; a rollback over hand edits succeeds and nothing is reported;
the identity cannot read a Secret or change anything.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from tests.k8s import _kube
from tests.k8s._kube import KUBECONFIG, ROOT, Kube, Pushed, _run, _tool, deploy_env

RELEASE = (
    "your-service-name-drift"  # contains the chart's name, so the fullname is the release name
)
NAMESPACE = f"{RELEASE}-kind"  # deploy-env.sh's <SERVICE_NAME>-<DEPLOY_ENV>
DRIFT_SA = "fst-drift"
DRIFT = ROOT / "scripts" / "drift.py"
EXTRA = f"{RELEASE}-hand-made"
FRONTEND = "spec.template.spec.containers[frontend]"


class Drift:
    """The release, its deploys and renders, and the drift check as the drift identity."""

    def __init__(self, work: Path, pushed: Pushed, admin: Kube) -> None:
        self.work, self.pushed, self.admin = work, pushed, admin
        self.app = Kube(NAMESPACE)
        _kube.deployer_kubectl(work / "bin", self.app.base[0])
        # The backend without its HPA, so the chart sets its replicas and a redeploy restores them.
        self.values = work / "drift-kind.yaml"
        self.values.write_text("hpa: {backend: {enabled: false}}\n")
        self.kubeconfig = work / "drift-kubeconfig"

    def deploy(self) -> None:
        rc, out = deploy_env(RELEASE, self.work, self.pushed, self.values)
        print(f"deploy-env: rc {rc}")
        assert rc == 0, out

    def identity(self) -> None:
        """A kubeconfig with only a short-lived token of the drift ServiceAccount, never printed."""
        token = self.app("create", "token", DRIFT_SA, "--duration", "20m").strip()
        cfg = json.loads(self.admin("config", "view", "--raw", "--minify", "-o", "json"))
        cluster = cfg["clusters"][0]["cluster"]
        doc = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [{"name": "fst", "cluster": cluster}],
            "users": [{"name": "drift", "user": {"token": token}}],
            "contexts": [{"name": "drift", "context": {"cluster": "fst", "user": "drift"}}],
            "current-context": "drift",
        }
        self.kubeconfig.touch(mode=0o600)
        self.kubeconfig.write_text(yaml.safe_dump(doc))

    def as_drift(self, *args: str) -> subprocess.CompletedProcess[str]:
        """kubectl as the drift identity, nothing else in its kubeconfig."""
        cmd = [self.app.base[0], "--kubeconfig", str(self.kubeconfig), "-n", NAMESPACE, *args]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("KUBE", "HELM_KUBE"))}
        return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)

    def check(self) -> set[tuple[str, str, str, str]]:
        """Render as the deploy did, then compare as the drift identity; the findings."""
        render, out = self.work / "render.yaml", self.work / "findings.json"
        rc, log = deploy_env(RELEASE, self.work, self.pushed, self.values, render=render)
        assert rc == 0, log
        assert "nothing deployed" in log
        kubectl = f"{self.app.base[0]} --kubeconfig {self.kubeconfig}"
        cmd = [sys.executable, str(DRIFT), "--rendered", str(render), "--release", RELEASE]
        cmd += ["--out", str(out), "--environment", "kind", "--kubectl", kubectl]
        env = {k: v for k, v in os.environ.items() if not k.startswith(("KUBE", "HELM_KUBE"))}
        done = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
        print(done.stdout, done.stderr)
        assert done.returncode == 0, done.stderr
        report = json.loads(out.read_text())
        return {(f["kind"], f["name"], f["path"], f["change"]) for f in report["findings"]}


def _bootstrap(values: Path) -> str:
    cmd = ["bash", str(ROOT / "scripts" / "k8s-bootstrap.sh"), "local"]
    return _run([*cmd, "--release", RELEASE, "--namespace", NAMESPACE, "--values", str(values)])


def _remove(admin: list[str], helm: list[str], objects: str) -> None:
    if _run([*helm, "status", RELEASE, "-n", NAMESPACE], check=False):
        _run([*helm, "uninstall", RELEASE, "-n", NAMESPACE, "--wait"], check=False)
    _run([*admin, "delete", "-f", "-", "--ignore-not-found", "--wait"], objects, check=False)


@pytest.fixture(scope="module")
def drift() -> Iterator[Drift]:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    work = Path(tempfile.mkdtemp(prefix="fst-drift-"))
    try:
        main = Kube()
        admin = main.base[:-2]  # without -n
        helm = [_tool("helm"), "--kubeconfig", str(KUBECONFIG), "--kube-context", _kube.CONTEXT]
        reader = work / "drift-reader.yaml"
        reader.write_text(
            yaml.safe_dump(
                {"drift": {"kind": "ServiceAccount", "name": DRIFT_SA, "namespace": NAMESPACE}}
            )
        )
        objects = _bootstrap(reader)
        assert "bootstrap-drift" in objects
        _remove(admin, helm, objects)  # what an interrupted run left behind
        tls = _kube.registry_tls(work / "tls")
        with _kube.registry(main, tls) as host:
            pushed = _kube.push_release_images(host, tls, "fst-drift")
            _run([*admin, "apply", "-f", "-"], objects)
            try:
                d = Drift(work, pushed, Kube(NAMESPACE))
                d.app("create", "serviceaccount", DRIFT_SA)
                d.identity()
                yield d
            finally:
                _remove(admin, helm, objects)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---- The lifecycle, in order ---------------------------------------------------------------------


def test_a_clean_deploy_reports_nothing(drift: Drift) -> None:
    drift.deploy()
    assert drift.check() == set()


# Each hand edit (kubectl as cluster admin), and the finding it must give, nothing else.
EDITS = {
    "kubectl scale": (
        ("scale", f"deployment/{RELEASE}-backend", "--replicas=2"),
        ("Deployment", f"{RELEASE}-backend", "spec.replicas", "changed"),
    ),
    "kubectl set image": (
        ("set", "image", f"deployment/{RELEASE}-frontend", "frontend=registry.invalid/hand-made:1"),
        ("Deployment", f"{RELEASE}-frontend", f"{FRONTEND}.image", "changed"),
    ),
    "kubectl set env": (
        ("set", "env", f"deployment/{RELEASE}-frontend", "-c", "frontend", "HOSTNAME=127.0.0.2"),
        ("Deployment", f"{RELEASE}-frontend", f"{FRONTEND}.env[HOSTNAME].value", "changed"),
    ),
    "a patched ConfigMap key": (
        (
            "patch",
            "configmap",
            f"{RELEASE}-config",
            "--type",
            "merge",
            "-p",
            '{"data":{"LOG_LEVEL":"WARNING"}}',
        ),
        ("ConfigMap", f"{RELEASE}-config", "data.LOG_LEVEL", "changed"),
    ),
    "a deleted object": (
        ("delete", "poddisruptionbudget", f"{RELEASE}-frontend"),
        ("PodDisruptionBudget", f"{RELEASE}-frontend", "", "missing"),
    ),
    "an extra labelled object": (
        ("create", "configmap", EXTRA, "--from-literal=x=y"),
        ("ConfigMap", EXTRA, "", "extra"),
    ),
}


@pytest.mark.parametrize("edit", list(EDITS))
def test_each_hand_edit_is_reported_and_a_redeploy_restores_git(drift: Drift, edit: str) -> None:
    """The edit is reported with its field path; the deploy takes the field back (it forces the
    conflict with kubectl's field manager), and the check then reports nothing. An extra object is
    not Helm's, so it is removed by hand: a redeploy leaves it."""
    args, finding = EDITS[edit]
    drift.app(*args)
    if edit == "an extra labelled object":
        drift.app("label", "configmap", EXTRA, f"app.kubernetes.io/instance={RELEASE}")
    assert drift.check() == {finding}
    if edit == "an extra labelled object":
        drift.app("delete", "configmap", EXTRA)
    drift.deploy()
    assert drift.check() == set()


def test_a_rollback_after_hand_edits_succeeds_and_restores_git(drift: Drift) -> None:
    """deploy-ops.yml's rollback (its flags), as the deployer, over fields kubectl now owns."""
    for edit in ("kubectl scale", "kubectl set image", "a patched ConfigMap key"):
        drift.app(*EDITS[edit][0])
    assert len(drift.check()) == 3  # noqa: PLR2004
    helm = _kube.helm_as_deployer()
    history = json.loads(_run([*helm, "history", RELEASE, "-n", NAMESPACE, "-o", "json"]))
    previous = str(history[-2]["revision"])
    _run(
        [
            *helm,
            "rollback",
            RELEASE,
            previous,
            "-n",
            NAMESPACE,
            "--force-conflicts",
            "--wait",
            "--timeout",
            "5m",
            "--history-max",
            "5",
        ]
    )
    assert drift.check() == set()


@pytest.mark.parametrize(
    "args",
    [
        ("get", "secrets"),
        ("get", "secret", f"{RELEASE}-secret"),
        ("get", "secrets", "--all-namespaces"),
        ("patch", "configmap", f"{RELEASE}-config", "--type", "merge", "-p", '{"data":{"A":"b"}}'),
        ("scale", f"deployment/{RELEASE}-backend", "--replicas=3"),
        ("delete", "configmap", f"{RELEASE}-config"),
        ("create", "configmap", "fst-drift-write", "--from-literal=x=y"),
    ],
)
def test_the_drift_identity_cannot_read_a_secret_or_change_anything(
    drift: Drift, args: tuple[str, ...]
) -> None:
    done = drift.as_drift(*args)
    assert done.returncode != 0, done.stdout[:200]
    assert "forbidden" in done.stderr.lower(), done.stderr
    assert drift.as_drift("auth", "can-i", "get", "configmaps").stdout.strip() == "yes"
