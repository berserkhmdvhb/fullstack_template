# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Graceful shutdown on the kind cluster fst: delete a backend or frontend Pod under steady load and
no request fails (wiki/infrastructure.md, Workloads: rollout, resources and shutdown).

Run with ``make kind-shutdown-test`` after ``make kind-up kind-deploy``; ``make test`` never
collects it (tests/conftest.py ignores tests/k8s). It talks to fst only, through
.local/kind/kubeconfig, and refuses any other context. The load generator is a Pod built from the
release's own ``helm test`` Job (its image, the backend's Python, its restricted securityContext
and labels) and sends its requests over keep-alive connections to the tier's Service (or the WAF's),
not a port-forward; the release's two
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
from urllib.parse import urlsplit, urlunsplit

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
CLIENTS = 8  # concurrent keep-alive connections
REPLICAS = 2  # ready Pods of the tier during the test

# The load generator, standard library only. CLIENTS threads, each holding one HTTP/1.1 keep-alive
# connection to the target (as a proxy's or a client's pool does) and sending its share of RATE.
# A connection the server closed cleanly (Connection: close, or a FIN seen before the next request)
# is replaced and is not a failure; a refused or reset connection, a RemoteDisconnected or any other
# error on a request is. Then one JSON summary line.
LOAD = r"""
import http.client, json, os, select, socket, threading, time, urllib.parse

url = urllib.parse.urlsplit(os.environ["TARGET"])
rate, duration = float(os.environ["RATE"]), float(os.environ["DURATION"])
timeout, clients = float(os.environ["TIMEOUT"]), int(os.environ["CLIENTS"])
path = url.path + (f"?{url.query}" if url.query else "")
lock = threading.Lock()
statuses, starts, answered, failures = {}, [], [], []
counts = {"connection_errors": 0, "timeouts": 0, "connections": 0, "clean_closes": 0}
expected = int(rate * duration)

def closed_by_server(conn):
    # A FIN (or an RST) already received on an idle connection: what a pool checks before reuse.
    if conn.sock is None:
        return True
    if not select.select([conn.sock], [], [], 0)[0]:
        return False
    try:
        return conn.sock.recv(1, socket.MSG_PEEK) == b""
    except OSError as e:
        record(time.time(), "connection_errors", f"idle connection: {e!r}")
        return True

def record(start, key, err=None):
    with lock:
        if key in counts:
            counts[key] += 1
        if err is not None:
            failures.append({"at": round(start, 3), "kind": key, "error": err})
        elif key not in counts:
            starts.append(start)
            statuses[key] = statuses.get(key, 0) + 1
            if not key.startswith("5"):
                answered.append(round(start, 2))

def client(c, t0):
    conn = None
    for k in range(c, expected, clients):
        time.sleep(max(0.0, t0 + k / rate - time.time()))
        if conn is not None and closed_by_server(conn):
            conn.close()
            conn = None
            record(time.time(), "clean_closes")
        if conn is None:
            conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
            record(time.time(), "connections")
        start = time.time()
        try:
            conn.request("GET", path)
            r = conn.getresponse()
            body = r.read()
            record(start, str(r.status))
            if r.status >= 500:
                with lock:
                    failures.append({"at": round(start, 3), "kind": str(r.status),
                                     "error": body[:200].decode(errors="replace")})
            if r.will_close:
                conn.close()
                conn = None
                record(start, "clean_closes")
            continue
        except TimeoutError as e:
            key, err = "timeouts", repr(e)
        except (OSError, http.client.HTTPException) as e:
            key, err = "connection_errors", repr(e)
        with lock:
            starts.append(start)
        record(start, key, err)
        conn.close()
        conn = None

t0 = time.time()
threads = [threading.Thread(target=client, args=(c, t0)) for c in range(clients)]
for t in threads:
    t.start()
for t in threads:
    t.join()
print(json.dumps({"expected": expected, "total": len(starts), "statuses": statuses, **counts,
                  "first": min(starts, default=None), "last": max(starts, default=None),
                  "failures": sorted(failures, key=lambda f: f["at"])[:20], "answered": answered}),
      flush=True)
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


def _target(job: dict[str, Any], tier: str, via: str) -> str:
    """A URL helm test checks: the backend's API route or the frontend's page, by Service; via the
    WAF, the API route on the WAF's Service (its upstream pool holds connections to the backend)."""
    env = {e["name"]: e["value"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    checks: dict[str, str] = dict(pair.split("=", 1) for pair in env["CHECK_200"].split())
    if via == "service":
        return checks["api-route" if tier == "backend" else "frontend"]
    if "waf-benign" not in checks:
        pytest.skip("the release has no WAF (kind-deploy turns it on in its last upgrade)")
    waf, api = urlsplit(checks["waf-benign"]), urlsplit(checks["api-route"])
    return urlunsplit((waf.scheme, waf.netloc, api.path, api.query, ""))


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


def _load_pod(job: dict[str, Any], target: str, name: str) -> dict[str, Any]:
    """The helm test Job's Pod template, renamed, running the load generator instead."""
    template = job["spec"]["template"]
    container = template["spec"]["containers"][0]
    env = {"TARGET": target, "RATE": RATE, "DURATION": DURATION, "TIMEOUT": TIMEOUT}
    env["CLIENTS"] = CLIENTS
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


TARGETS = [("backend", "service"), ("frontend", "service"), ("backend", "waf")]

# Each tier's interpreter (the images are distroless) prints the Pod's TCP tables.
READ_TCP = {
    "backend": [
        "/app/.venv/bin/python",
        "-c",
        "import glob\nfor f in glob.glob('/proc/net/tcp*'): print(open(f).read())",
    ],
    "frontend": [
        "/nodejs/bin/node",
        "-e",
        (
            "for (const f of ['tcp', 'tcp6']) try { process.stdout.write("
            "require('fs').readFileSync('/proc/net/' + f, 'utf8')) } catch {}"
        ),
    ],
}


def _load_connections(kube: Kube, tier: str, pod: str, sources: list[str]) -> int:
    """Established connections to the Pod's http port from the load's sources (the load Pod, or
    the WAF Pods). A client connects only to send a request, so one means the Pod served load."""
    spec = kube.get("pod", pod)["spec"]["containers"][0]
    port = next(p["containerPort"] for p in spec["ports"] if p["name"] == "http")
    # /proc/net/tcp{,6}: hex addresses, an IPv4 one little-endian (also the tail of a mapped IPv6).
    peers = {"".join(f"{int(b):02X}" for b in reversed(ip.split("."))) for ip in sources}
    count = 0
    for line in kube("exec", pod, "-c", spec["name"], "--", *READ_TCP[tier]).splitlines():
        fields = line.split()
        if fields[3:4] != ["01"]:  # 01: ESTABLISHED
            continue
        local, remote = fields[1].split(":"), fields[2].split(":")
        count += int(local[1], 16) == port and remote[0][-8:] in peers
    return count


def _victim(kube: Kube, tier: str, via: str, load: str) -> tuple[str, bool]:
    """A ready Pod that served load: the first, else the other one; neither is inconclusive."""
    if via == "waf":
        waf = kube.get(
            "pods", "-l", f"app.kubernetes.io/component=waf,app.kubernetes.io/instance={RELEASE}"
        )
        sources = [p["status"]["podIP"] for p in waf["items"]]
    else:
        sources = [kube.get("pod", load)["status"]["podIP"]]
    pods = _ready_pods(kube, tier)
    for pod in pods:
        if _load_connections(kube, tier, pod, sources):
            return pod, pod == pods[0]
    pytest.fail(f"inconclusive: no ready {tier} Pod ({pods}) has a connection from {sources}")


def _delete_under_load(kube: Kube, job: dict[str, Any], tier: str, via: str) -> dict[str, Any]:
    """Delete a ready Pod of the tier under load, with its default grace period; the load's
    summary and the times, once the Pod is gone and replaced."""
    target = _target(job, tier, via)
    load = f"{RELEASE}-shutdown-load-{uuid.uuid4().hex[:8]}"
    terminating = False
    try:
        kube("create", "-f", "-", stdin=json.dumps(_load_pod(job, target, load)))
        kube("wait", "--for=condition=Ready", f"pod/{load}", "--timeout=120s")
        time.sleep(LEAD)
        victim, first_choice = _victim(kube, tier, via, load)
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
        # On a failure before the summary was read (a timeout above), its log still shows.
        if "summary" not in locals():
            print(f"{load} log: {kube('logs', load, check=False).strip()}")
        kube("delete", "pod", load, "--ignore-not-found", "--wait=false", check=False)
    answered = summary.pop("answered")
    run = {
        "target": target,
        "victim": victim,
        # False: the first ready Pod held no load connection, so the other one was deleted.
        "victim_was_first_choice": first_choice,
        "deleted": deleted,
        "gone": gone_at,
        "replacement_ready": ready_at,
        "terminating": terminating,
        # Every answer below 500, 429 included: the Pod answered it on that connection.
        "answered": len(answered),
        "answered_before_delete": sum(t < deleted for t in answered),
        "answered_after_gone": sum(t > gone_at for t in answered),
        "summary": summary,
    }
    run["failed"] = (
        summary["connection_errors"]
        + summary["timeouts"]
        + sum(n for code, n in summary["statuses"].items() if code.startswith("5"))
    )
    print(json.dumps(run))
    return run


@pytest.mark.parametrize(("tier", "via"), TARGETS)
@pytest.mark.usefixtures("two_ready")
def test_pod_deletion_under_load_fails_no_request(
    kube: Kube, test_job: dict[str, Any], tier: str, via: str
) -> None:
    run = _delete_under_load(kube, test_job, tier, via)
    summary, report = run["summary"], json.dumps(run)
    assert run["failed"] == 0, f"failed requests (5xx, connection errors, timeouts): {report}"
    assert summary["total"] >= 0.8 * RATE * DURATION, f"the load fell short: {report}"
    assert run["answered"] >= 0.8 * RATE * DURATION, f"too few answers: {report}"
    assert run["answered_before_delete"] >= RATE, f"too few answers before the delete: {report}"
    assert run["answered_after_gone"] >= RATE, f"too few answers after the Pod was gone: {report}"
    assert summary["first"] <= run["deleted"] - LEAD / 2, f"no load before the delete: {report}"
    assert summary["last"] >= max(run["gone"], run["replacement_ready"]), f"ended early: {report}"
    assert run["terminating"], f"{run['victim']} was never seen Terminating: {report}"
