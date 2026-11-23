# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Graceful shutdown on the kind cluster fst: delete a backend or frontend Pod under steady load and
no request fails (wiki/infrastructure.md, Workloads: rollout, resources and shutdown).

Run with ``make kind-shutdown-test`` after ``make kind-up kind-deploy``; ``make test`` never
collects it (tests/conftest.py ignores tests/k8s). It talks to fst only, through
.local/kind/kubeconfig, and refuses any other context. The load generator is a Pod built from the
release's own ``helm test`` Job (its image, the backend's Python, its restricted securityContext
and labels) and sends its requests to the tier's Service, not a port-forward; the release's two
test NetworkPolicies (``helm get hooks``) admit it for the test's duration, as they admit the test
Job during ``helm test``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
KUBECONFIG = ROOT / ".local" / "kind" / "kubeconfig"
CONTEXT = "kind-fst"
# The release and namespace scripts/kind-deploy.sh installs.
RELEASE = "your-service-name"
NAMESPACE = f"{RELEASE}-local"
RATE = float(os.environ.get("SHUTDOWN_TEST_RATE", "20"))  # requests per second
DURATION = float(os.environ.get("SHUTDOWN_TEST_DURATION", "40"))  # seconds
LEAD = 5.0  # seconds of load before the delete
TIMEOUT = 5.0  # seconds per request
REPLICAS = 2  # ready Pods of the tier during the test

# The load generator, standard library only: a new connection per request (so the Service picks a
# Pod each time), at a fixed rate, then one JSON summary line.
LOAD = r"""
import http.client, json, os, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

url, rate = os.environ["TARGET"], float(os.environ["RATE"])
duration, timeout = float(os.environ["DURATION"]), float(os.environ["TIMEOUT"])
lock = threading.Lock()
statuses, starts, failures = {}, [], []
counts = {"connection_errors": 0, "timeouts": 0}

def one():
    start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            r.read()
            key = str(r.status)
    except urllib.error.HTTPError as e:
        key = str(e.code)
    except TimeoutError as e:
        key, err = "timeouts", repr(e)
    except urllib.error.URLError as e:
        key = "timeouts" if isinstance(e.reason, TimeoutError) else "connection_errors"
        err = repr(e.reason)
    except (OSError, http.client.HTTPException) as e:
        key, err = "connection_errors", repr(e)
    with lock:
        starts.append(start)
        if key in counts:
            counts[key] += 1
            failures.append({"at": round(start, 3), "kind": key, "error": err})
        else:
            statuses[key] = statuses.get(key, 0) + 1

expected = int(rate * duration)
t0 = time.time()
with ThreadPoolExecutor(max_workers=64) as pool:
    for i in range(expected):
        time.sleep(max(0.0, t0 + i / rate - time.time()))
        pool.submit(one)
print(json.dumps({"expected": expected, "total": len(starts), "statuses": statuses, **counts,
                  "first": min(starts, default=None), "last": max(starts, default=None),
                  "failures": sorted(failures, key=lambda f: f["at"])[:20]}), flush=True)
"""


def _run(cmd: list[str], stdin: str | None = None, *, check: bool = True) -> str:
    # KUBECONFIG is always fst's file, and helm's own context variables are dropped.
    env = {k: v for k, v in os.environ.items() if not k.startswith("HELM_KUBE")}
    env["KUBECONFIG"] = str(KUBECONFIG)
    done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, check=False)
    if check and done.returncode:
        pytest.fail(f"{' '.join(cmd[:6])} ... failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


def _tool(name: str) -> str:
    path = shutil.which(name)
    assert path, f"no {name} on PATH (scripts/tools.sh install {name}; make puts .tools/bin first)"
    return path


class Kube:
    """kubectl on fst's namespace, with the kubectl pin of tools/kind-nodes.tsv for the server."""

    def __init__(self) -> None:
        any_kubectl = _tool("kubectl")
        config = [any_kubectl, "--kubeconfig", str(KUBECONFIG), "config"]
        current = _run([*config, "current-context"]).strip()
        assert current == CONTEXT, f"refusing: the current context is {current!r}, not {CONTEXT}"
        contexts = _run([*config, "get-contexts", "-o", "name"]).split()
        assert contexts == [CONTEXT], f"refusing: {KUBECONFIG} holds other contexts: {contexts}"
        server = json.loads(_run([any_kubectl, "--context", CONTEXT, "version", "-o", "json"]))
        minor = "".join(c for c in server["serverVersion"]["minor"] if c.isdigit())
        rows = (ROOT / "tools" / "kind-nodes.tsv").read_text().splitlines()
        pins = [r.split("\t")[1] for r in rows if not r.startswith("#") and f":v1.{minor}." in r]
        assert pins, f"no kubectl pin for a 1.{minor} server in tools/kind-nodes.tsv"
        self.base = [_tool(pins[0]), "--kubeconfig", str(KUBECONFIG), "--context", CONTEXT]
        self.base += ["-n", NAMESPACE]

    def __call__(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        return _run([*self.base, *args], stdin, check=check)

    def get(self, *args: str) -> dict[str, Any]:
        out = self("get", *args, "--ignore-not-found", "-o", "json")
        return json.loads(out) if out else {}


@pytest.fixture(scope="module")
def kube() -> Kube:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    return Kube()


@pytest.fixture(scope="module")
def test_job(kube: Kube) -> Iterator[dict[str, Any]]:
    """The release's helm test Job; its two NetworkPolicies are applied for the module."""
    helm = [_tool("helm"), "--kubeconfig", str(KUBECONFIG), "--kube-context", CONTEXT]
    hooks = _run([*helm, "get", "hooks", RELEASE, "-n", NAMESPACE])
    docs = [d for d in yaml.safe_load_all(hooks) if d]
    job = next(d for d in docs if d["kind"] == "Job" and d["metadata"]["name"] == f"{RELEASE}-test")
    policies = [d for d in docs if d["kind"] == "NetworkPolicy"]
    names = sorted(p["metadata"]["name"] for p in policies)
    assert names == [f"{RELEASE}-test-egress", f"{RELEASE}-test-ingress"], names
    manifest = json.dumps({"apiVersion": "v1", "kind": "List", "items": policies})
    try:
        kube("apply", "-f", "-", stdin=manifest)
        yield job
    finally:
        kube("delete", "-f", "-", "--ignore-not-found", stdin=manifest, check=False)


def _target(job: dict[str, Any], tier: str) -> str:
    """The URL helm test checks: the backend's API route, or the frontend's page (by Service)."""
    env = {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    checks: dict[str, str] = dict(pair.split("=", 1) for pair in env["CHECK_200"].split())
    return checks["api-route" if tier == "backend" else "frontend"]


def _ready_pods(kube: Kube, tier: str) -> list[str]:
    selector = f"app.kubernetes.io/component={tier},app.kubernetes.io/instance={RELEASE}"
    return [
        p["metadata"]["name"]
        for p in kube.get("pods", "-l", selector).get("items", [])
        if not p["metadata"].get("deletionTimestamp")
        and any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"]["conditions"])
    ]


def _wait(what: str, cond: Callable[[], bool], timeout: float) -> float:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return time.time()
        time.sleep(0.25)
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}")


@pytest.fixture
def two_ready(kube: Kube, tier: str) -> Iterator[None]:
    """At least two ready Pods of the tier; the replica count and the HPA floor restored after."""
    name = f"{RELEASE}-{tier}"
    replicas = kube.get("deployment", name)["spec"]["replicas"]
    floor = kube.get("hpa", name).get("spec", {}).get("minReplicas")
    try:
        if floor is not None and floor < REPLICAS:
            kube(
                "patch",
                "hpa",
                name,
                "--type=merge",
                "-p",
                f'{{"spec":{{"minReplicas":{REPLICAS}}}}}',
            )
        if replicas < REPLICAS:
            kube("scale", "deployment", name, f"--replicas={REPLICAS}")
        _wait(
            f"{REPLICAS} ready {tier} Pods", lambda: len(_ready_pods(kube, tier)) >= REPLICAS, 300
        )
        yield
    finally:
        if floor is not None and floor < REPLICAS:
            patch = json.dumps({"spec": {"minReplicas": floor}})
            kube("patch", "hpa", name, "--type=merge", "-p", patch, check=False)
        kube("scale", "deployment", name, f"--replicas={replicas}", check=False)
        kube("rollout", "status", "deployment", name, "--timeout=300s", check=False)


def _load_pod(job: dict[str, Any], tier: str, name: str) -> dict[str, Any]:
    """The helm test Job's Pod template, renamed, running the load generator instead."""
    template = job["spec"]["template"]
    container = template["spec"]["containers"][0]
    env = {"TARGET": _target(job, tier), "RATE": RATE, "DURATION": DURATION, "TIMEOUT": TIMEOUT}
    load = {
        **container,
        "name": "load",
        "command": [container["command"][0], "-I", "-c", LOAD],
        "env": [{"name": k, "value": str(v)} for k, v in env.items()],
    }
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": template["metadata"]["labels"],
        },
        "spec": {**template["spec"], "restartPolicy": "Never", "containers": [load]},
    }


@pytest.mark.parametrize("tier", ["backend", "frontend"])
@pytest.mark.usefixtures("two_ready")
def test_pod_deletion_under_load_fails_no_request(
    kube: Kube, test_job: dict[str, Any], tier: str
) -> None:
    load = f"{RELEASE}-shutdown-load-{uuid.uuid4().hex[:8]}"
    terminating = False
    try:
        kube("create", "-f", "-", stdin=json.dumps(_load_pod(test_job, tier, load)))
        kube("wait", "--for=condition=Ready", f"pod/{load}", "--timeout=120s")
        time.sleep(LEAD)
        victim = _ready_pods(kube, tier)[0]
        kube("delete", "pod", victim, "--wait=false")
        deleted = time.time()

        def gone() -> bool:
            nonlocal terminating
            pod = kube.get("pod", victim)
            terminating = terminating or bool(pod.get("metadata", {}).get("deletionTimestamp"))
            return not pod

        gone_at = _wait(f"{victim} to be gone", gone, DURATION + 60)
        ready_at = _wait(
            f"a replacement {tier} Pod ready", lambda: len(_ready_pods(kube, tier)) >= REPLICAS, 180
        )
        succeeded = "--for=jsonpath={.status.phase}=Succeeded"
        kube("wait", succeeded, f"pod/{load}", f"--timeout={DURATION + 120:.0f}s")
        summary = json.loads(kube("logs", load).strip().splitlines()[-1])
    finally:
        kube("delete", "pod", load, "--ignore-not-found", "--wait=false", check=False)

    times = {"deleted": deleted, "gone": gone_at, "replacement_ready": ready_at}
    report = json.dumps({"tier": tier, "victim": victim, **times, "summary": summary})
    print(report)
    errors = sum(n for code, n in summary["statuses"].items() if code.startswith("5"))
    assert errors == 0, f"{errors} 5xx answers: {report}"
    assert summary["connection_errors"] == 0, f"connection errors: {report}"
    assert summary["timeouts"] == 0, f"timeouts: {report}"
    assert summary["total"] >= 0.8 * RATE * DURATION, f"the load fell short: {report}"
    assert summary["first"] <= deleted - LEAD / 2, f"no load well before the delete: {report}"
    assert summary["last"] >= max(gone_at, ready_at), f"the load ended too early: {report}"
    assert terminating, f"{victim} was never seen Terminating: {report}"
