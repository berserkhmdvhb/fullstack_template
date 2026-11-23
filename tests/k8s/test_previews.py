# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Pull request previews on the kind cluster fst (wiki/ci-cd.md, "Pull request previews").

Run with ``make kind-preview-test`` after ``make kind-up kind-deploy``; ``make test`` never collects
it (tests/conftest.py ignores tests/k8s). It talks to fst only, through .local/kind/kubeconfig. The
preview environment is bootstrapped as the admin does it (scripts/k8s-bootstrap.sh preview: the
preview namespace, its database namespace, quota, LimitRange, the shared ServiceAccount and the
preview deployer's Role), for a release name of its own, so its namespaces are its own. Then, as
the preview deployer and through the real scripts: scripts/ci/deploy-env.sh deploys two pull
requests' previews (its test-only DEPLOY_ENV=kind with PREVIEW_PR and the chart's
values.preview.yaml; the kind job's images by digest from the throwaway registry), redeploys one,
and scripts/ci/preview-cleanup.sh removes them. Everything is removed at the end, the registry too.

One lifecycle, in file order (the make target stops at the first failure).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.k8s import _kube
from tests.k8s._kube import CHART, KUBECONFIG, ROOT, Kube, Pushed, _run, _tool, deploy_env

SERVICE = "your-service-name-pv"  # contains the chart's name, so each fullname is its release name
NAMESPACE = f"{SERVICE}-preview"  # deploy-env.sh's and preview-cleanup.sh's preview namespace
DB_NAMESPACE = f"{NAMESPACE}-db"
PREVIEW_VALUES = CHART / "values.preview.yaml"
CLEANUP = ROOT / "scripts" / "ci" / "preview-cleanup.sh"
INSTANCE = "app.kubernetes.io/instance"
ANY_STATE = ("--deployed", "--failed", "--pending", "--uninstalling")  # helm 4 has no --all


def release(pr: int) -> str:
    return f"{SERVICE}-pr-{pr}"


class Previews:
    """The scripts as the preview deployer, and what they left behind (read as cluster admin)."""

    def __init__(self, work: Path, pushed: Pushed, helm: list[str]) -> None:
        self.app, self.db = Kube(NAMESPACE), Kube(DB_NAMESPACE)
        self.work, self.pushed, self.helm = work, pushed, helm
        self.user = _kube.deployer("preview")
        assert self.user != _kube.deployer(), "the preview deployer must be its own identity"
        _kube.deployer_kubectl(work / "bin", self.app.base[0], self.user)

    def deploy(self, pr: int, *values: Path) -> tuple[int, str]:
        started = time.time()
        rc, out = deploy_env(
            SERVICE, self.work, self.pushed, PREVIEW_VALUES, *values, user=self.user, preview_pr=pr
        )
        names = [v.name for v in values]
        print(f"deploy-env PREVIEW_PR={pr} {names}: rc {rc}, {time.time() - started:.0f}s")
        print(out)
        return rc, out

    def cleanup(self, *args: str, **env: str) -> tuple[int, str]:
        started = time.time()
        done = subprocess.run(
            ["bash", str(CLEANUP), *args],  # noqa: S607 - bash from PATH, as the workflow runs it
            capture_output=True,
            text=True,
            env=_kube.deployer_env(self.work, self.user) | {"SERVICE_NAME": SERVICE} | env,
            check=False,
        )
        out = done.stdout + done.stderr
        print(f"preview-cleanup {args} {env}: rc {done.returncode}, {time.time() - started:.0f}s")
        print(out)
        return done.returncode, out

    def as_deployer(self, *args: str) -> subprocess.CompletedProcess[str]:
        """kubectl (``kubectl ...``) or helm (``helm ...``) as the preview deployer."""
        env = _kube.deployer_env(self.work, self.user)
        cmd = [str(self.work / "bin" / "kubectl"), *args[1:]] if args[0] == "kubectl" else []
        if args[0] == "helm":
            cmd = [_tool("helm"), *args[1:]]
        return subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)

    def releases(self) -> dict[str, dict[str, Any]]:
        out = _run([*self.helm, "list", "-n", NAMESPACE, *ANY_STATE, "-o", "json"])
        return {r["name"]: r for r in json.loads(out or "[]")}

    def pods(self, pr: int) -> dict[str, dict[str, Any]]:
        """The live Pods of a preview by uid (its Jobs' aside)."""
        found = self.app.get(
            "pods", "-l", f"{INSTANCE}={release(pr)},!batch.kubernetes.io/job-name"
        )
        items = found.get("items", [])
        return {
            p["metadata"]["uid"]: p for p in items if not p["metadata"].get("deletionTimestamp")
        }

    def left(self, pr: int) -> list[str]:
        """What is left of a preview: its Jobs and Pods in the preview namespace, its claims in the
        database namespace."""
        sel = f"{INSTANCE}={release(pr)}"
        found = self.app.get("jobs,pods,services,deployments", "-l", sel).get("items", [])
        found += self.db.get("pvc,statefulsets", "-l", sel).get("items", [])
        return [f"{i['kind']}/{i['metadata']['name']}" for i in found]


def _ready(pod: dict[str, Any]) -> bool:
    conditions = pod.get("status", {}).get("conditions", [])
    return any(c["type"] == "Ready" and c["status"] == "True" for c in conditions)


def _remove(admin: list[str], helm: list[str], objects: str) -> None:
    """Every release in the preview namespace, then the bootstrap's objects (the namespaces too)."""
    out = _run([*helm, "list", "-n", NAMESPACE, *ANY_STATE, "-q"], check=False)
    for name in out.split():
        _run([*helm, "uninstall", name, "-n", NAMESPACE, "--wait"], check=False)
    _run([*admin, "delete", "-f", "-", "--ignore-not-found", "--wait"], objects, check=False)


@pytest.fixture(scope="module")
def previews() -> Iterator[Previews]:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    work = Path(tempfile.mkdtemp(prefix="fst-previews-"))
    try:
        main = Kube()
        admin = main.base[:-2]  # without -n, for the bootstrap's objects
        helm = [_tool("helm"), "--kubeconfig", str(KUBECONFIG), "--kube-context", _kube.CONTEXT]
        objects = _run(
            ["bash", str(ROOT / "scripts" / "k8s-bootstrap.sh"), "preview", "--release", SERVICE]
        )
        _remove(admin, helm, objects)  # what an interrupted run left behind
        tls = _kube.registry_tls(work / "tls")
        with _kube.registry(main, tls) as host:
            pushed = _kube.push_release_images(host, tls, "fst-previews")
            _run([*admin, "apply", "-f", "-"], objects)
            try:
                yield Previews(work, pushed, helm)
            finally:
                _remove(admin, helm, objects)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---- The lifecycle, in order ---------------------------------------------------------------------


def test_two_previews_deploy_into_the_one_namespace_and_each_serves_on_its_own_services(
    previews: Previews,
) -> None:
    """Two pull requests, two releases in the preview namespace, each verified and smoke tested
    through its own backend Service; verify checks each release's own Pods only."""
    for pr in (1, 2):
        rc, out = previews.deploy(pr)
        assert rc == 0, out
        assert f"Deployed, verified and smoke tested: {release(pr)} in kind" in out
        assert f"Verifying release {release(pr)} in {NAMESPACE}" in out
        checked = out.split("== Check 3", 1)[1].split("== Check 5", 1)[0]  # the Pods and digests
        other = release(3 - pr)
        assert release(pr) in checked
        assert other not in checked, f"verify of {release(pr)} looked at {other}'s Pods"
    found = previews.releases()
    assert {n: r["status"] for n, r in found.items()} == {
        release(1): "deployed",
        release(2): "deployed",
    }
    for pr in (1, 2):
        pods = previews.pods(pr)
        assert len(pods) >= 2, pods  # noqa: PLR2004 - a backend and a frontend
        assert all(_ready(p) for p in pods.values())
        for name in ("backend", "frontend"):
            svc = previews.app.get("service", f"{release(pr)}-{name}")
            assert svc["spec"]["selector"][INSTANCE] == release(pr)
            endpoints = previews.app.get(
                "endpointslices", "-l", f"kubernetes.io/service-name={release(pr)}-{name}"
            )
            assert any(e.get("endpoints") for e in endpoints.get("items", [])), (pr, name)
        routes = previews.app.get("httproutes", "-l", f"{INSTANCE}={release(pr)}")["items"]
        hosts = sorted(h for r in routes for h in r["spec"]["hostnames"])
        assert hosts == sorted(f"{release(pr)}{s}.invalid" for s in ("", "-api", "-app")), (
            hosts
        )  # its own hostnames; PREVIEW_DOMAIN is unset
    # Both share the bootstrap's ServiceAccount, which neither release owns.
    sa = previews.app.get("serviceaccount", "your-service-name")
    assert "meta.helm.sh/release-name" not in sa["metadata"].get("annotations", {})


def test_a_synchronize_redeploys_one_preview_and_leaves_the_other_alone(
    previews: Previews,
) -> None:
    """A new push to pull request 1: its release is upgraded (revision 2) with the new chart
    values, and pull request 2's release and Pods are untouched."""
    before = previews.pods(2)
    change = previews.work / "sync.yaml"
    change.write_text("config:\n  LOG_LEVEL: INFO\n")
    rc, out = previews.deploy(1, change)
    assert rc == 0, out
    assert "last deployed revision before this deploy: 1" in out
    found = previews.releases()
    assert (found[release(1)]["revision"], found[release(1)]["status"]) == ("2", "deployed")
    assert (found[release(2)]["revision"], found[release(2)]["status"]) == ("1", "deployed")
    assert set(previews.pods(2)) == set(before), "the other preview's Pods were replaced"


def test_the_database_quota_holds_the_previews_and_refuses_one_more_than_it_allows(
    previews: Previews,
) -> None:
    """The bootstrap's database-namespace quota (dbQuota: quotaReleases databases) holds the two
    previews; as the preview deployer, a third release's volume claim fits, a fourth's is refused by
    the quota."""
    quota = previews.db.get("resourcequota", "bootstrap-quota")
    used, hard = quota["status"]["used"], quota["status"]["hard"]
    print(f"database quota: used {used}, hard {hard}")
    assert (used["pods"], used["persistentvolumeclaims"]) == ("2", "2")
    claim = previews.db.get("pvc", "-l", f"{INSTANCE}={release(2)}")["items"][0]["spec"]
    created = []
    for pr in (3, 4):
        manifest = previews.work / f"claim-{pr}.json"
        name = f"postgresql-data-{release(pr)}-postgresql-0"
        spec = {k: claim[k] for k in ("accessModes", "storageClassName", "resources")}
        manifest.write_text(
            json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "PersistentVolumeClaim",
                    "metadata": {"name": name, "labels": {INSTANCE: release(pr)}},
                    "spec": spec,
                }
            )
        )
        done = previews.as_deployer("kubectl", "-n", DB_NAMESPACE, "create", "-f", str(manifest))
        print(f"release {pr}'s claim: rc {done.returncode} {done.stdout}{done.stderr}")
        if pr == 3:  # noqa: PLR2004 - the third of quotaReleases 3
            assert done.returncode == 0, done.stderr
            created.append(name)
        else:
            assert done.returncode != 0, done.stdout
            assert "exceeded quota: bootstrap-quota" in done.stderr, done.stderr
    for name in created:
        previews.db("delete", "pvc", name, "--wait")


def test_the_preview_deployer_is_refused_outside_the_preview_namespaces(previews: Previews) -> None:
    """By the API server: no namespace creation, and no access to the main release (another
    namespace), through kubectl or helm. And by the cleanup script, before any call."""
    main_ns = _kube.NAMESPACE
    for args in (
        ("kubectl", "create", "namespace", f"{SERVICE}-pr-9"),
        ("kubectl", "-n", main_ns, "get", "secrets"),
        (
            "kubectl",
            "-n",
            main_ns,
            "delete",
            "deployment",
            f"{_kube.RELEASE}-backend",
            "--dry-run=server",
        ),
        ("helm", "list", "-n", main_ns),
        ("helm", "uninstall", _kube.RELEASE, "-n", main_ns, "--dry-run"),
    ):
        done = previews.as_deployer(*args)
        assert done.returncode != 0, (args, done.stdout)
        assert "forbidden" in done.stderr.lower(), (args, done.stderr)
    rc, out = previews.cleanup("--pr", "1", NAMESPACE=main_ns)
    assert rc != 0
    assert f"refusing namespace {main_ns}" in out
    assert previews.releases()[release(1)]["status"] == "deployed"


def test_closing_a_pull_request_removes_its_preview_and_leaves_the_other(
    previews: Previews,
) -> None:
    """preview.yml's close (or label removal) path: the release, its kept migration Jobs and its
    database volume go; the other preview keeps serving."""
    assert previews.left(1), "nothing of pull request 1's preview to remove"
    before = previews.pods(2)
    rc, out = previews.cleanup("--pr", "1")
    assert rc == 0, out
    assert f"Removed the preview {release(1)}" in out
    assert release(1) not in previews.releases()
    deadline = time.time() + 120
    while previews.left(1) and time.time() < deadline:
        time.sleep(2)
    assert previews.left(1) == []
    assert previews.releases()[release(2)]["status"] == "deployed"
    after = previews.pods(2)
    assert set(after) == set(before)
    assert all(_ready(p) for p in after.values())


def test_the_ttl_sweep_keeps_an_open_fresh_preview_and_removes_an_expired_one(
    previews: Previews,
) -> None:
    """The scheduled sweep: pull request 2 is open and was deployed today, so a 7-day TTL keeps it;
    a TTL of 0 days makes it expired, and the sweep removes it."""
    rc, out = previews.cleanup("--sweep", OPEN_PRS="2", PREVIEW_TTL_DAYS="7")
    assert rc == 0, out
    assert f"{release(2)}: kept" in out
    assert previews.releases()[release(2)]["status"] == "deployed"
    rc, out = previews.cleanup("--sweep", OPEN_PRS="2", PREVIEW_TTL_DAYS="0")
    assert rc == 0, out
    assert f"{release(2)}: last deployed 0 days ago" in out
    assert previews.releases() == {}
    deadline = time.time() + 120
    while previews.left(2) and time.time() < deadline:
        time.sleep(2)
    assert previews.left(2) == []
