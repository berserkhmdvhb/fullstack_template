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
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
import yaml

from tests.k8s._kube import (
    CONTEXT,
    KUBECONFIG,
    NAMESPACE,
    RELEASE,
    Kube,
    _run,
    _tool,
    upgraded,
)

RATE = float(os.environ.get("SHUTDOWN_TEST_RATE", "20"))  # requests per second
DURATION = float(os.environ.get("SHUTDOWN_TEST_DURATION", "40"))  # seconds
LEAD = 5.0  # seconds of load before the delete
TIMEOUT = 5.0  # seconds per request
CLIENTS = 8  # concurrent keep-alive connections
REPLICAS = 2  # ready Pods of the tier during the test
WRITE_RATE = float(os.environ.get("SHUTDOWN_TEST_WRITE_RATE", "5"))  # POSTs per second, WAF case
WRITERS = 2  # concurrent keep-alive connections of the writes
# The backend's limit for the module's run only (a helm upgrade, rolled back after): one client
# sends the whole load, so the release's default (100/minute) would answer most of it with 429.
RATE_LIMIT = os.environ.get("SHUTDOWN_TEST_RATE_LIMIT", "100000/minute")

# The load generator, standard library only. CLIENTS threads, each holding one HTTP/1.1 keep-alive
# connection to the target (as a proxy's or a client's pool does) and sending its share of RATE.
# A connection the server closed cleanly (Connection: close, or a FIN seen before the next request)
# is replaced and is not a failure; a refused or reset connection, a RemoteDisconnected or any other
# error on a request is. With WRITE_RATE above 0, WRITERS more threads do the same with POSTs of a
# new item each (a unique name), counted apart; after the load, each failed write's name is looked
# up, so the summary says whether the backend stored it anyway. Then one JSON summary line.
LOAD = r"""
import http.client, json, os, select, socket, threading, time, urllib.parse

url = urllib.parse.urlsplit(os.environ["TARGET"])
rate, duration = float(os.environ["RATE"]), float(os.environ["DURATION"])
timeout, clients = float(os.environ["TIMEOUT"]), int(os.environ["CLIENTS"])
write_rate, writers = float(os.environ["WRITE_RATE"]), int(os.environ["WRITERS"])
path = url.path + (f"?{url.query}" if url.query else "")
items, prefix = os.environ["WRITE_PATH"], os.environ["WRITE_PREFIX"]
lock = threading.Lock()

def stats(rate):
    counts = {"connection_errors": 0, "timeouts": 0, "connections": 0, "clean_closes": 0}
    return {"statuses": {}, "starts": [], "answered": [], "failures": [],
            "expected": int(rate * duration), "counts": counts}

reads, writes = stats(rate), stats(write_rate)

def record(st, start, key, err=None, name=None):
    with lock:
        if key in st["counts"]:
            st["counts"][key] += 1
        if err is not None:
            st["failures"].append({"at": round(start, 3), "kind": key, "error": err, "name": name})
        elif key not in st["counts"]:
            st["starts"].append(start)
            st["statuses"][key] = st["statuses"].get(key, 0) + 1
            if not key.startswith("5"):
                st["answered"].append(round(start, 2))

def closed_by_server(st, conn):
    # A FIN (or an RST) already received on an idle connection: what a pool checks before reuse.
    if conn.sock is None:
        return True
    if not select.select([conn.sock], [], [], 0)[0]:
        return False
    try:
        return conn.sock.recv(1, socket.MSG_PEEK) == b""
    except OSError as e:
        record(st, time.time(), "connection_errors", f"idle connection: {e!r}")
        return True

def client(st, c, n, every, t0, request):
    conn = None
    for k in range(c, st["expected"], n):
        time.sleep(max(0.0, t0 + k / every - time.time()))
        if conn is not None and closed_by_server(st, conn):
            conn.close()
            conn = None
            record(st, time.time(), "clean_closes")
        if conn is None:
            conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
            record(st, time.time(), "connections")
        start, name = time.time(), f"{prefix}-{k}"
        try:
            request(conn, name)
            r = conn.getresponse()
            body = r.read()
            record(st, start, str(r.status))
            if r.status >= 500:
                with lock:
                    st["failures"].append({"at": round(start, 3), "kind": str(r.status),
                                           "error": body[:200].decode(errors="replace"),
                                           "name": name})
            if r.will_close:
                conn.close()
                conn = None
                record(st, start, "clean_closes")
            continue
        except TimeoutError as e:
            key, err = "timeouts", repr(e)
        except (OSError, http.client.HTTPException) as e:
            key, err = "connection_errors", repr(e)
        with lock:
            st["starts"].append(start)
        record(st, start, key, err, name)
        conn.close()
        conn = None

def get(conn, name):
    conn.request("GET", path)

def post(conn, name):
    conn.request("POST", items, json.dumps({"name": name}), {"Content-Type": "application/json"})

def stored(name):
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=timeout)
    conn.request("GET", f"{items}?search={urllib.parse.quote(name)}")
    r = conn.getresponse()
    found = r.status == 200 and any(i["name"] == name for i in json.loads(r.read())["items"])
    conn.close()
    return found

t0 = time.time()
threads = [threading.Thread(target=client, args=(reads, c, clients, rate, t0, get))
           for c in range(clients)]
if write_rate:
    threads += [threading.Thread(target=client, args=(writes, c, writers, write_rate, t0, post))
                for c in range(writers)]
for t in threads:
    t.start()
for t in threads:
    t.join()
for f in writes["failures"]:
    f["stored"] = stored(f["name"]) if f["name"] else None

def summary(st):
    return {"expected": st["expected"], "total": len(st["starts"]), "statuses": st["statuses"],
            **st["counts"], "first": min(st["starts"], default=None),
            "last": max(st["starts"], default=None), "answered": st["answered"],
            "failures": sorted(st["failures"], key=lambda f: f["at"])[:20]}

print(json.dumps({**summary(reads), "writes": summary(writes) if write_rate else None}), flush=True)
"""


@pytest.fixture(scope="module")
def kube() -> Kube:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    return Kube()


@pytest.fixture(scope="module")
def raised_limit(kube: Kube) -> Iterator[None]:  # noqa: ARG001 - its context guard runs first
    """RATE_LIMIT_DEFAULT raised for the module, then the release rolled back (_kube.upgraded)."""
    with upgraded("--set-string", f"config.RATE_LIMIT_DEFAULT={RATE_LIMIT}"):
        yield


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


def _load_pod(job: dict[str, Any], target: str, name: str, write_rate: float) -> dict[str, Any]:
    """The helm test Job's Pod template, renamed, running the load generator instead."""
    template = job["spec"]["template"]
    container = template["spec"]["containers"][0]
    env = {"TARGET": target, "RATE": RATE, "DURATION": DURATION, "TIMEOUT": TIMEOUT}
    env |= {"CLIENTS": CLIENTS, "WRITE_RATE": write_rate, "WRITERS": WRITERS, "WRITE_PREFIX": name}
    env["WRITE_PATH"] = urlsplit(target).path  # the API route, tests.apiPath: the items collection
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


def _counted(summary: dict[str, Any], deleted: float, gone: float) -> dict[str, Any]:
    """The answers of one kind of request (reads or writes) around the delete, and its failures."""
    answered = summary.pop("answered")
    statuses = summary["statuses"]
    return {
        # Every answer below 500, 429 included: the Pod answered it on that connection.
        "answered": len(answered),
        "answered_before_delete": sum(t < deleted for t in answered),
        "answered_after_gone": sum(t > gone for t in answered),
        "share_429": round(statuses.get("429", 0) / max(1, sum(statuses.values())), 3),
        "failed": summary["connection_errors"]
        + summary["timeouts"]
        + sum(n for code, n in statuses.items() if code.startswith("5")),
        "summary": summary,
    }


def _delete_under_load(kube: Kube, job: dict[str, Any], tier: str, via: str) -> dict[str, Any]:
    """Delete a ready Pod of the tier under load, with its default grace period; the load's
    summary and the times, once the Pod is gone and replaced. Through the WAF, writes too."""
    target = _target(job, tier, via)
    load = f"{RELEASE}-shutdown-load-{uuid.uuid4().hex[:8]}"
    write_rate = WRITE_RATE if via == "waf" else 0.0
    terminating = False
    try:
        pod = _load_pod(job, target, load, write_rate)
        kube("create", "-f", "-", stdin=json.dumps(pod))
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
    writes = summary.pop("writes")
    run = {
        "target": target,
        "victim": victim,
        # False: the first ready Pod held no load connection, so the other one was deleted.
        "victim_was_first_choice": first_choice,
        "deleted": deleted,
        "gone": gone_at,
        "replacement_ready": ready_at,
        "terminating": terminating,
        **_counted(summary, deleted, gone_at),
        "writes": writes and _counted(writes, deleted, gone_at),
    }
    print(json.dumps(run))
    return run


@pytest.mark.parametrize(("tier", "via"), TARGETS)
@pytest.mark.usefixtures("raised_limit", "two_ready")
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
    writes = run["writes"]
    if via != "waf":
        return
    # Each failure names its write and whether the backend stored it anyway (the summary's
    # failures): a 5xx or a reset is a failure either way, as the client cannot tell.
    assert writes["failed"] == 0, f"failed writes (5xx, connection errors, timeouts): {report}"
    # Every write was created: a 4xx (401, 405, 422, 429) would mean none reached the create path.
    assert set(writes["summary"]["statuses"]) == {"201"}, f"writes not created: {report}"
    expected = WRITE_RATE * DURATION
    assert writes["summary"]["total"] >= 0.8 * expected, f"the writes fell short: {report}"
    assert writes["answered_before_delete"] >= WRITE_RATE, f"too few writes before: {report}"
    assert writes["answered_after_gone"] >= WRITE_RATE, f"too few writes after: {report}"
