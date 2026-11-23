# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tracing and the Redis rate-limit storage on the kind cluster fst, through the chart's own values and
the backend's NetworkPolicy (wiki/backend-architecture.md, Tracing; wiki/security.md, Backend
Rate Limiting).

Run with ``make kind-tracing-redis-test`` after ``make kind-up kind-deploy``; ``make test`` never
collects it (tests/conftest.py ignores tests/k8s). It talks to fst only, through
.local/kind/kubeconfig, and refuses any other context. It installs a release of its own
(``RELEASE`` in ``NAMESPACE`` and its database namespace, bootstrapped as cluster admin, deployed as
the bootstrap's deployer with values.local.yaml and the images of the kind job's release, removed
at the end), so it never disturbs the kind job's main release. In ``NAMESPACE``, next to the
release, a throwaway OpenTelemetry Collector (OTLP/HTTP in, its debug exporter's log out) and a
throwaway Redis, both pinned by digest. The release reaches them through
``observability.tracing.*``, the app Secret's ``RATE_LIMIT_STORAGE`` (``secrets.existingSecret``)
and ``backend.networkPolicy.extraEgress``. Its
requests go through port-forwards to each backend Pod (the client is 127.0.0.1 on both, so both
count one client's hits). Test manipulation (the throwaway objects, scaling Redis, a Pod restart)
runs as cluster admin, as in the other kind tests.

Plaintext ``redis://`` to a non-loopback host is refused in production (settings.py); this release
runs values.local.yaml's ``ENV: development``, so the refusal does not apply: on kind the Redis is
a Pod of the test's namespace, behind the NetworkPolicy, with nothing on the wire but test counters.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.k8s._kube import KUBECONFIG, ROOT, Kube, _run, helm_as_deployer
from tests.k8s._kube import NAMESPACE as MAIN_NAMESPACE
from tests.k8s._kube import RELEASE as MAIN_RELEASE

CHART = ROOT / "charts" / "your-service-name"
VALUES = yaml.safe_load((CHART / "values.yaml").read_text())
# Contains the chart's name, so the fullname is the release name.
RELEASE = "your-service-name-feat"
NAMESPACE = f"{RELEASE}-local"
DB_NAMESPACE = f"{NAMESPACE}-db"
API = f"/api/{VALUES['config']['API_VERSION']}"
COLLECTOR = "fst-otel-collector"
REDIS = "fst-redis"
# renovate: image=docker.io/otel/opentelemetry-collector  # noqa: ERA001 - an annotation, not code
COLLECTOR_TAG = "0.165.0@sha256:34b24b740c3c88d3467740e8642a397c13898ef76d7295c5425164c482ed1b73"
# renovate: image=docker.io/library/redis  # noqa: ERA001 - an annotation, not code
REDIS_TAG = "8.8.4-alpine@sha256:1273af45b299d2b8b60d0e400d90aa6c2c5b1794f01d6a7ca60ff3ba6e65efb5"
LIMIT = 30  # the release's RATE_LIMIT_DEFAULT, per hour: a burst never crosses its window
BURST = LIMIT + 10
SPAN_WAIT = 30.0  # seconds for a span to reach the collector (the batch processor's delay is 5)
QUIET = 15.0  # seconds a blocked export is given to show up anyway

# The collector's config: OTLP/HTTP in, every span's attributes in its log.
COLLECTOR_CONFIG = """
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
exporters:
  debug:
    verbosity: detailed
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [debug]
"""
# The restricted profile the bootstrapped namespace enforces.
RESTRICTED = {
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]},
    "runAsNonRoot": True,
    "seccompProfile": {"type": "RuntimeDefault"},
}
EGRESS = {
    COLLECTOR: {
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": COLLECTOR}},
        "port": 4318,
    },
    REDIS: {"podSelector": {"matchLabels": {"app.kubernetes.io/name": REDIS}}, "port": 6379},
}


def _egress(*names: str) -> dict[str, Any]:
    """A values file's backend.networkPolicy.extraEgress: a rule to each named throwaway."""
    rules = [
        {
            "to": [{"podSelector": EGRESS[n]["podSelector"]}],
            "ports": [{"protocol": "TCP", "port": EGRESS[n]["port"]}],
        }
        for n in names
    ]
    return {"backend": {"networkPolicy": {"extraEgress": rules}}}


def _workload(
    name: str,
    image: str,
    port: int,
    uid: int,
    volumes: list[dict[str, Any]],
    **container: Any,  # noqa: ANN401
) -> list[dict[str, Any]]:
    """A one-replica Deployment under the restricted profile and its Service."""
    labels = {"app.kubernetes.io/name": name}
    pod = {
        "metadata": {"labels": labels},
        "spec": {
            "automountServiceAccountToken": False,
            "securityContext": {"runAsUser": uid, "runAsGroup": uid},
            "containers": [
                {
                    "name": name,
                    "image": image,
                    "ports": [{"containerPort": port}],
                    "readinessProbe": {"tcpSocket": {"port": port}, "periodSeconds": 2},
                    "securityContext": RESTRICTED,
                    **container,
                }
            ],
            "volumes": volumes,
        },
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "labels": labels},
        "spec": {"replicas": 1, "selector": {"matchLabels": labels}, "template": pod},
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "labels": labels},
        "spec": {"selector": labels, "ports": [{"port": port, "targetPort": port}]},
    }
    return [deployment, service]


def _throwaways(app_secret: dict[str, str], db_password: str) -> tuple[str, str]:
    """The app namespace's objects (the collector, Redis, their ingress policy, the app Secret)
    and the database namespace's (its password Secret), as two List manifests."""
    collector = _workload(
        COLLECTOR,
        f"docker.io/otel/opentelemetry-collector:{COLLECTOR_TAG}",
        4318,
        10001,
        [{"name": "conf", "configMap": {"name": COLLECTOR}}],
        args=["--config=/conf/config.yaml"],
        volumeMounts=[{"name": "conf", "mountPath": "/conf"}],
    )
    redis = _workload(
        REDIS,
        f"docker.io/library/redis:{REDIS_TAG}",
        6379,
        999,
        [{"name": "data", "emptyDir": {}}],
        args=["redis-server", "--save", "", "--appendonly", "no", "--protected-mode", "no"],
        volumeMounts=[{"name": "data", "mountPath": "/data"}],
    )
    # The chart's default-deny covers the whole namespace: the backend's Pods may reach these two.
    backend = {"matchLabels": {"app.kubernetes.io/component": "backend"}}
    ingress = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "fst-allow-backend-to-throwaways"},
        "spec": {
            "podSelector": {
                "matchExpressions": [
                    {
                        "key": "app.kubernetes.io/name",
                        "operator": "In",
                        "values": [COLLECTOR, REDIS],
                    }
                ]
            },
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"podSelector": backend}]}],
        },
    }
    conf = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": COLLECTOR},
        "data": {"config.yaml": COLLECTOR_CONFIG},
    }
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": f"{RELEASE}-app"},
        "stringData": app_secret,
    }
    db_secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": f"{RELEASE}-db"},
        "stringData": {VALUES["postgresql"]["existingSecretKey"]: db_password},
    }
    app = [conf, *collector, *redis, ingress, secret]
    return (
        json.dumps({"apiVersion": "v1", "kind": "List", "items": app}),
        json.dumps({"apiVersion": "v1", "kind": "List", "items": [db_secret]}),
    )


def _wait(what: str, cond: Callable[[], bool], timeout: float, every: float = 1.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(every)
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}")


class Release:
    """The test's release, its two namespaces and the throwaways next to it."""

    def __init__(self, work: Path) -> None:
        self.app, self.db = Kube(NAMESPACE), Kube(DB_NAMESPACE)
        self.cluster_wide = self.app.base[:-2]
        self.helm = helm_as_deployer()
        self.work = work
        self.image_args = self._image_args()

    def _image_args(self) -> list[str]:
        """The kind job's images (its release's values), as test_migrations.py reads them."""
        helm_admin = self.helm[: self.helm.index("--kube-as-user")]
        cmd = [*helm_admin, "get", "values", MAIN_RELEASE, "-n", MAIN_NAMESPACE, "-o", "json"]
        main = json.loads(_run(cmd))
        args: list[str] = []
        for image_id, image in sorted(main.get("images", {}).items()):
            for key in ("registry", "repository", "tag"):
                args += ["--set-string", f"images.{image_id}.{key}={image.get(key, '')}"]
        assert args, f"the release {MAIN_RELEASE} has no image values: run make kind-deploy first"
        return args

    def deploy(self, *egress: str) -> None:
        """helm upgrade --install as the deployer (kind-deploy.sh's flags), tracing to the
        collector, the limit in Redis, two backend replicas, and an extraEgress rule to each of
        ``egress``."""
        values = self.work / "egress.yaml"
        values.write_text(yaml.safe_dump(_egress(*egress)))
        cmd = [*self.helm, "upgrade", "--install", RELEASE, str(CHART), "-n", NAMESPACE]
        cmd += ["-f", str(CHART / "values.local.yaml"), "-f", str(values), *self.image_args]
        cmd += ["--set", "observability.tracing.enabled=true"]
        cmd += ["--set-string", f"observability.tracing.endpoint=http://{COLLECTOR}:4318"]
        cmd += ["--set-string", f"secrets.existingSecret={RELEASE}-app"]
        cmd += ["--set-string", f"postgresql.existingSecret={RELEASE}-db"]
        cmd += ["--set-string", f"config.RATE_LIMIT_DEFAULT={LIMIT}/hour"]
        cmd += ["--set", "replicas.backend=2", "--set", "hpa.backend.minReplicas=2"]
        cmd += ["--history-max", "5", "--wait", "--wait-for-jobs", "--timeout", "9m"]
        started = time.time()
        out = _run(cmd)
        assert "forbidden" not in out.lower(), out
        took = time.time() - started
        print(f"deploy (extraEgress to {', '.join(egress) or 'nothing'}): {took:.0f}s")

    def backend_pods(self) -> list[str]:
        selector = f"app.kubernetes.io/component=backend,app.kubernetes.io/instance={RELEASE}"
        pods = self.app.get("pods", "-l", selector).get("items", [])
        ready = [
            p["metadata"]["name"]
            for p in pods
            if not p["metadata"].get("deletionTimestamp")
            and any(
                c["type"] == "Ready" and c["status"] == "True" for c in p["status"]["conditions"]
            )
        ]
        return sorted(ready)

    def ready(self, name: str) -> bool:
        return bool(self.app.get("deployment", name).get("status", {}).get("readyReplicas"))

    def collector_log(self) -> str:
        return self.app("logs", f"deployment/{COLLECTOR}", check=False)

    def flush_redis(self) -> None:
        self.app("exec", f"deployment/{REDIS}", "--", "redis-cli", "FLUSHALL")

    def restart(self, name: str) -> None:
        """A new Pod of the throwaway (its old connections reset), ready."""
        self.app("delete", "pod", "-l", f"app.kubernetes.io/name={name}", "--wait=true")
        _wait(f"{name} ready again", lambda: self.ready(name), 120)


@pytest.fixture(scope="module")
def release(request: pytest.FixtureRequest) -> Iterator[Release]:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    with tempfile.TemporaryDirectory() as work:
        r = Release(Path(work))
        boot = ["bash", str(ROOT / "scripts" / "k8s-bootstrap.sh"), "local"]
        objects = _run([*boot, "--release", RELEASE, "--namespace", NAMESPACE])
        _remove(r, objects)  # what an interrupted run left behind
        db_password = secrets.token_hex(24)
        app_secret = {
            "DB_USER": VALUES["postgresql"]["auth"]["user"],
            "DB_PASSWORD": db_password,
            "STATS_PASS": secrets.token_hex(24),
            "RATE_LIMIT_STORAGE": f"redis://{REDIS}:6379/0",
        }
        app, db = _throwaways(app_secret, db_password)
        try:
            _run([*r.cluster_wide, "apply", "-f", "-"], objects)
            r.app("apply", "-f", "-", stdin=app)
            r.db("apply", "-f", "-", stdin=db)
            _wait(
                "the collector and Redis ready", lambda: r.ready(COLLECTOR) and r.ready(REDIS), 300
            )
            r.deploy(COLLECTOR, REDIS)
            yield r
        finally:
            if request.session.testsfailed:
                for name in (COLLECTOR, REDIS):
                    log = r.app("logs", f"deployment/{name}", "--tail=200", check=False)
                    print(f"--- {name} log\n{log}")
            _remove(r, objects)


def _remove(r: Release, objects: str) -> None:
    """The release and the bootstrap's objects (the namespaces, the throwaways with them)."""
    helm_admin = r.helm[: r.helm.index("--kube-as-user")]
    if _run([*helm_admin, "status", RELEASE, "-n", NAMESPACE], check=False):
        _run([*r.helm, "uninstall", RELEASE, "-n", NAMESPACE, "--wait"], check=False)
    cmd = [*r.cluster_wide, "delete", "-f", "-", "--ignore-not-found", "--wait"]
    _run(cmd, objects, check=False)


@contextmanager
def _forward(r: Release, pod: str) -> Iterator[str]:
    """A port-forward to the backend Pod's http port; yields its base URL."""
    spec = r.app.get("pod", pod)["spec"]["containers"][0]
    port = next(p["containerPort"] for p in spec["ports"] if p["name"] == "http")
    # The caller's HOME (kubectl caches under it), fst's kubeconfig, no helm context variables.
    env = {k: v for k, v in os.environ.items() if not k.startswith("HELM_KUBE")}
    env["KUBECONFIG"] = str(KUBECONFIG)
    cmd = [*r.app.base, "port-forward", "--address", "127.0.0.1", f"pod/{pod}", f":{port}"]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        found = re.search(r"Forwarding from 127\.0\.0\.1:(\d+)", line)
        assert found, f"the port-forward to {pod} did not start: {line}"
        yield f"http://127.0.0.1:{found.group(1)}"
    finally:
        proc.kill()
        proc.wait()


def _get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, json.loads(body) if body.startswith(b"{") else {}


def _spans(log: str) -> list[dict[str, Any]]:
    """The debug exporter's spans: trace id, name, kind and attributes of each."""
    spans = []
    for block in log.split("Span #")[1:]:
        fields = dict(re.findall(r"^\s*(Trace ID|Name|Kind)\s*: (.*)$", block, re.MULTILINE))
        attrs = dict(re.findall(r"^\s*-> ([\w.]+): \w+\((.*)\)$", block, re.MULTILINE))
        # A statement can span lines; the exporter prints another attribute after it.
        statement = re.search(r"-> db\.statement: Str\((.*?)\)\n\s*-> ", block, re.DOTALL)
        if statement:
            attrs["db.statement"] = statement.group(1)
        spans.append({**fields, "attributes": attrs, "text": block})
    return spans


def _trace(r: Release, trace_id: str, *, wait: float = SPAN_WAIT) -> list[dict[str, Any]]:
    """The collector's spans of the trace, once at least one server span arrived (or [])."""
    end = time.time() + wait
    while True:
        spans = [s for s in _spans(r.collector_log()) if s.get("Trace ID") == trace_id]
        if any(s.get("Kind") == "Server" for s in spans) or time.time() >= end:
            return spans
        time.sleep(1)


def _traceparent() -> tuple[str, dict[str, str]]:
    trace_id = secrets.token_hex(16)
    return trace_id, {"traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01"}


@pytest.fixture(autouse=True)
def _fresh_limit(release: Release) -> None:
    """Every test starts with an empty counter (the limit is per hour)."""
    release.flush_redis()


def test_a_request_produces_a_server_span_with_its_route_and_a_db_span_without_parameters(
    release: Release,
) -> None:
    marker = f"fst-marker-{secrets.token_hex(8)}"
    trace_id, headers = _traceparent()
    with _forward(release, release.backend_pods()[0]) as base:
        status, _ = _get(f"{base}{API}/items?search={marker}", headers)
    assert status == HTTPStatus.OK
    spans = _trace(release, trace_id)
    server = [s for s in spans if s.get("Kind") == "Server"]
    assert server, f"no server span of {trace_id} reached the collector"
    assert server[0]["attributes"].get("http.route") == f"{API}/items", server[0]["text"]
    db = [s for s in spans if any(k.startswith("db.") for k in s["attributes"])]
    assert db, f"no database span in the trace: {[s.get('Name') for s in spans]}"
    statements = [s["attributes"].get("db.statement", "") for s in db]
    # The search's statements carry its placeholder ($1), never its value.
    assert any(re.search(r"\$\d", st) for st in statements), statements
    assert marker not in release.collector_log(), "a parameter value reached the collector"


def test_a_problem_responses_trace_id_is_its_spans(release: Release) -> None:
    with _forward(release, release.backend_pods()[0]) as base:
        status, body = _get(f"{base}{API}/items/{uuid.uuid4()}")
    assert status == HTTPStatus.NOT_FOUND, body
    assert re.fullmatch(r"[0-9a-f]{32}", body.get("trace_id", "")), body
    server = [s for s in _trace(release, body["trace_id"]) if s.get("Kind") == "Server"]
    assert server, f"no server span with the problem's trace_id {body['trace_id']}"
    attrs = server[0]["attributes"]
    status_code = attrs.get("http.response.status_code", attrs.get("http.status_code"))
    assert status_code == "404", server[0]["text"]


def test_the_limit_is_shared_by_both_replicas(release: Release) -> None:
    pods = release.backend_pods()
    assert len(pods) == 2, pods  # noqa: PLR2004
    statuses = _burst(release, pods)
    print(f"shared limit: {statuses}")
    ok = [s for pod in pods for s in statuses[pod] if s == HTTPStatus.OK]
    assert len(ok) == LIMIT, f"{len(ok)} of {BURST} answered 200, the limit is {LIMIT}: {statuses}"
    for pod in pods:  # each Pod refused once the other had used the shared count up
        assert HTTPStatus.TOO_MANY_REQUESTS in statuses[pod], statuses
        assert set(statuses[pod]) <= {HTTPStatus.OK, HTTPStatus.TOO_MANY_REQUESTS}, statuses


def _burst(release: Release, pods: list[str]) -> dict[str, list[int]]:
    """BURST requests to the API route, alternating between the Pods; each Pod's statuses."""
    statuses: dict[str, list[int]] = {pod: [] for pod in pods}
    with _forward(release, pods[0]) as first, _forward(release, pods[1]) as second:
        bases = dict(zip(pods, (first, second), strict=True))
        for k in range(BURST):
            pod = pods[k % 2]
            statuses[pod].append(_get(f"{bases[pod]}{API}/items")[0])
    return statuses


def test_with_redis_gone_the_api_fails_open_and_the_limit_returns_with_it(release: Release) -> None:
    pods = release.backend_pods()
    release.app("scale", f"deployment/{REDIS}", "--replicas=0")
    try:
        _wait(
            "Redis gone",
            lambda: (
                not release.app.get("pods", "-l", f"app.kubernetes.io/name={REDIS}").get("items")
            ),
            120,
        )
        down = _burst(release, pods)
        print(f"Redis down: {down}")
        assert all(s == HTTPStatus.OK for codes in down.values() for s in codes), down
    finally:
        release.app("scale", f"deployment/{REDIS}", "--replicas=1")
    _wait("Redis back", lambda: release.ready(REDIS), 120)
    # Each Pod's first request after the return still fails open (its pooled connection was to
    # the old Redis Pod; redis-py drops it and reconnects on the next call), then a fresh count.
    for pod in pods:
        with _forward(release, pod) as base:
            assert _get(f"{base}{API}/items")[0] == HTTPStatus.OK
    release.flush_redis()
    back = _burst(release, pods)
    print(f"Redis back: {back}")
    assert sum(s == HTTPStatus.OK for codes in back.values() for s in codes) == LIMIT, back


def test_without_the_extra_egress_rule_no_span_arrives_and_requests_still_answer(
    release: Release,
) -> None:
    """The NetworkPolicy drops the export; the batch processor's thread waits it out, not the
    request. The collector is restarted so no connection made before the change outlives it."""
    try:
        release.deploy(REDIS)
        release.restart(COLLECTOR)
        trace_id, headers = _traceparent()
        with _forward(release, release.backend_pods()[0]) as base:
            started = time.time()
            statuses = [_get(f"{base}{API}/items", headers)[0] for _ in range(5)]
            took = time.time() - started
        print(f"without the rule: {statuses} in {took:.1f}s")
        assert statuses == [200] * 5, statuses
        assert took < 5, f"five requests took {took:.1f}s"  # noqa: PLR2004
        assert _trace(release, trace_id, wait=QUIET) == [], "a span arrived through the policy"
    finally:
        release.deploy(COLLECTOR, REDIS)
    trace_id, headers = _traceparent()
    with _forward(release, release.backend_pods()[0]) as base:
        assert _get(f"{base}{API}/items", headers)[0] == HTTPStatus.OK
    assert _trace(release, trace_id), "no span after the rule was restored"
