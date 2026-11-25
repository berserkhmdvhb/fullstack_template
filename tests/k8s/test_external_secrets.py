# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
The app Secret from the External Secrets Operator on the kind cluster fst (wiki/security.md,
OpenBao; wiki/gke-cluster-setup.md, External Secrets).

Run with ``make kind-external-secrets-test`` after ``make kind-up kind-deploy``; ``make test``
never collects it (tests/conftest.py ignores tests/k8s). It talks to fst only, through
.local/kind/kubeconfig, and refuses any other context. As cluster admin it installs the pinned
operator (tools/pins.tsv, external-secrets-chart) with the values the GKE guide uses
(deploy/external-secrets/external-secrets-values.yaml), and OpenBao in dev mode (``OPENBAO_TAG``,
by digest), whose root token is generated here, held in a Secret of OpenBao's namespace and read
only inside its container: never printed, never written to a file. OpenBao gets Kubernetes auth
with one role per environment, bound to that environment's namespaces, and a policy that reads
only that environment's path; each namespace gets the store
deploy/external-secrets/secretstore-openbao.yaml (the server and the role replaced, no CA: dev mode
serves http). The release (``RELEASE`` in ``NAMESPACE`` and its database namespace, bootstrapped as
cluster admin, deployed as the bootstrap's deployer with values.local.yaml and the kind job's
images) turns ``secrets.externalSecret`` on with only the store values set to OpenBao. A namespace
of another environment (``OTHER``) has a store of its own, which reads its own path and is refused
the release's. Everything is removed at the end; the images come from ghcr.io, and nothing else
leaves the runner.
"""

from __future__ import annotations

import base64
import json
import secrets
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.k8s._kube import CONTEXT, KUBECONFIG, ROOT, Kube, _run, _tool, helm_as_deployer
from tests.k8s._kube import NAMESPACE as MAIN_NAMESPACE
from tests.k8s._kube import RELEASE as MAIN_RELEASE
from tests.supply_chain._pins import pin

CHART = ROOT / "charts" / "your-service-name"
VALUES = yaml.safe_load((CHART / "values.yaml").read_text())
STORES = ROOT / "deploy" / "external-secrets"
OPERATOR_VALUES = STORES / "external-secrets-values.yaml"
# Contains the chart's name, so the fullname is the release name.
RELEASE = "your-service-name-eso"
ENV = "local"
NAMESPACE = f"{RELEASE}-{ENV}"
DB_NAMESPACE = f"{NAMESPACE}-db"
OTHER = "fst-eso-other"  # another environment's namespace
OPERATOR_NAMESPACE = "fst-external-secrets"
OPERATOR_RELEASE = "external-secrets"
OPENBAO_NAMESPACE = "fst-openbao"
OPENBAO = "openbao"
OPENBAO_URL = f"http://{OPENBAO}.{OPENBAO_NAMESPACE}.svc:8200"
# The ClusterRoleBinding OpenBao reviews tokens with.
TOKEN_REVIEW = "fst-openbao-token-review"  # noqa: S105 - a name, not a password
# renovate: image=ghcr.io/openbao/openbao  # noqa: ERA001 - an annotation, not code
OPENBAO_TAG = "2.7.2@sha256:5046911075e137e154ce49ab051502f4ec7cf367d484ae4a4dce665d9b90561f"
STORE = "openbao"  # the store's name in deploy/external-secrets/secretstore-openbao.yaml
SERVICE = "your-service-name"
# The values the switch to OpenBao changes, and nothing else (wiki/security.md, OpenBao).
STORE_VALUES = {
    "secrets": {
        "externalSecret": {
            "enabled": True,
            "secretStoreRef": {"name": STORE, "kind": "SecretStore"},
            "remoteKeyTemplate": f"{SERVICE}/{ENV}/{{key}}",
            "remoteProperty": "value",
        }
    }
}
RESTRICTED = {
    "allowPrivilegeEscalation": False,
    "capabilities": {"drop": ["ALL"]},
    "runAsNonRoot": True,
    "seccompProfile": {"type": "RuntimeDefault"},
}
# Inside OpenBao's container: the root token from its environment, never from an argument.
BAO = 'export BAO_ADDR=http://127.0.0.1:8200 BAO_TOKEN="$BAO_DEV_ROOT_TOKEN_ID"; '


def _wait(what: str, cond: Callable[[], bool], timeout: float, every: float = 2.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(every)
    pytest.fail(f"timed out after {timeout:.0f}s waiting for {what}")


def _ready(obj: dict[str, Any]) -> tuple[str, str]:
    """An External Secrets object's Ready condition: (status, reason), or ("", "")."""
    for c in obj.get("status", {}).get("conditions", []):
        if c.get("type") == "Ready":
            return str(c.get("status", "")), str(c.get("reason", ""))
    return "", ""


def _openbao_objects(token: str) -> str:
    """OpenBao in dev mode (in memory), its token reviewer binding and Service, as one List."""
    labels = {"app.kubernetes.io/name": OPENBAO}
    meta = {"name": OPENBAO, "namespace": OPENBAO_NAMESPACE}
    root = {"name": "fst-openbao-root", "namespace": OPENBAO_NAMESPACE}
    container = {
        "name": OPENBAO,
        "image": f"ghcr.io/openbao/openbao:{OPENBAO_TAG}",
        "command": ["bao", "server", "-dev", "-dev-no-store-token"],
        "args": ["-dev-listen-address=0.0.0.0:8200"],
        "env": [
            {
                "name": "BAO_DEV_ROOT_TOKEN_ID",
                "valueFrom": {"secretKeyRef": {"name": root["name"], "key": "token"}},
            },
            {"name": "HOME", "value": "/tmp"},  # noqa: S108 - an emptyDir
        ],
        "ports": [{"containerPort": 8200}],
        "readinessProbe": {"httpGet": {"path": "/v1/sys/health", "port": 8200}, "periodSeconds": 2},
        "securityContext": RESTRICTED,
        "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],  # noqa: S108 - an emptyDir
    }
    items = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": OPENBAO_NAMESPACE}},
        {"apiVersion": "v1", "kind": "Secret", "metadata": root, "stringData": {"token": token}},
        {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": meta},
        {
            # OpenBao checks each login's token with the TokenReview API, as its own identity.
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": TOKEN_REVIEW},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "system:auth-delegator",
            },
            "subjects": [{"kind": "ServiceAccount", **meta}],
        },
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {**meta, "labels": labels},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {"labels": labels},
                    "spec": {
                        "serviceAccountName": OPENBAO,
                        "securityContext": {"runAsUser": 100, "runAsGroup": 1000},
                        "containers": [container],
                        "volumes": [{"name": "tmp", "emptyDir": {}}],
                    },
                },
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {**meta, "labels": labels},
            "spec": {"selector": labels, "ports": [{"port": 8200, "targetPort": 8200}]},
        },
    ]
    return json.dumps({"apiVersion": "v1", "kind": "List", "items": items})


def _policy(env: str) -> str:
    """An environment's policy: read its own path of the KV engine, nothing else."""
    return f'path "secret/data/{SERVICE}/{env}/*" {{ capabilities = ["read"] }}\n'


def _store(role: str) -> str:
    """deploy/external-secrets/secretstore-openbao.yaml for one namespace: the kind server, the
    namespace's role, and no CA (OpenBao's dev mode serves http)."""
    docs = [d for d in yaml.safe_load_all((STORES / "secretstore-openbao.yaml").read_text()) if d]
    store = next(d for d in docs if d["kind"] == "SecretStore")
    assert store["metadata"]["name"] == STORE, store["metadata"]
    vault = store["spec"]["provider"]["vault"]
    vault["server"] = OPENBAO_URL
    vault.pop("caProvider")
    vault["auth"]["kubernetes"]["role"] = role
    return json.dumps({"apiVersion": "v1", "kind": "List", "items": docs})


class Setup:
    """The operator, OpenBao, the release's namespaces and OTHER, as cluster admin."""

    def __init__(self, work: Path) -> None:
        self.app, self.db = Kube(NAMESPACE), Kube(DB_NAMESPACE)
        self.other, self.bao = Kube(OTHER), Kube(OPENBAO_NAMESPACE)
        self.cluster_wide = self.app.base[:-2]
        self.helm_admin = [_tool("helm"), "--kubeconfig", str(KUBECONFIG)]
        self.helm_admin += ["--kube-context", CONTEXT]
        self.helm = helm_as_deployer()
        self.work = work
        self.values = {
            "DB_USER": VALUES["postgresql"]["auth"]["user"],
            "DB_PASSWORD": secrets.token_hex(24),
            "STATS_PASS": secrets.token_hex(24),
        }
        self.other_value = secrets.token_hex(24)

    def install_operator(self) -> None:
        version = pin("external-secrets-chart")
        chart = ROOT / ".tools" / "opt" / f"external-secrets-chart-{version}" / "external-secrets"
        assert chart.is_dir(), f"no {chart}: scripts/tools.sh install external-secrets-chart"
        meta = yaml.safe_load((chart / "Chart.yaml").read_text())
        image_tag = yaml.safe_load(OPERATOR_VALUES.read_text())["image"]["tag"]
        assert meta["version"] == version, meta
        # The images pinned beside the chart are its own release's.
        assert image_tag.split("@")[0] == meta["appVersion"], (image_tag, meta["appVersion"])
        cmd = [*self.helm_admin, "install", OPERATOR_RELEASE, str(chart), "-n", OPERATOR_NAMESPACE]
        cmd += ["--create-namespace", "-f", str(OPERATOR_VALUES), "--wait", "--timeout", "5m"]
        _run(cmd)

    def bao_sh(self, script: str, *args: str, stdin: str | None = None) -> str:
        """A shell script in OpenBao's container, as root token holder (its environment only)."""
        cmd = ["exec", "-i" if stdin is not None else "-q", f"deployment/{OPENBAO}", "--"]
        return self.bao(*cmd, "sh", "-c", BAO + script, "sh", *args, stdin=stdin)

    def install_openbao(self) -> None:
        token = secrets.token_urlsafe(32)
        _run([*self.cluster_wide, "apply", "-f", "-"], _openbao_objects(token))
        self.bao("rollout", "status", f"deployment/{OPENBAO}", "--timeout=180s")
        self.bao_sh(
            "bao auth enable kubernetes >/dev/null && bao write auth/kubernetes/config"
            ' kubernetes_host="https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT"'
            " >/dev/null"
        )
        # One policy and one role per environment: the release's namespaces, and OTHER.
        for env, namespaces in ((ENV, f"{NAMESPACE},{DB_NAMESPACE}"), ("other", OTHER)):
            role = NAMESPACE if env == ENV else OTHER
            self.bao_sh('bao policy write "$1" - >/dev/null', env, stdin=_policy(env))
            self.bao_sh(
                'bao write "auth/kubernetes/role/$1" bound_service_account_names=external-secrets'
                ' bound_service_account_namespaces="$2" token_policies="$3" audience=openbao'
                " token_ttl=10m >/dev/null",
                role,
                namespaces,
                env,
            )
        # Each value through stdin, never an argument (`value=-`).
        put = 'bao kv put -mount=secret "$1" value=- >/dev/null'
        keys = VALUES["secrets"]["externalSecret"]["keys"]
        for key, value in self.values.items():
            self.bao_sh(put, f"{SERVICE}/{ENV}/{keys[key]}", stdin=value)
        self.bao_sh(put, f"{SERVICE}/other/{keys['DB_PASSWORD']}", stdin=self.other_value)

    def stores(self) -> None:
        for kube, role in ((self.app, NAMESPACE), (self.db, NAMESPACE), (self.other, OTHER)):
            kube("apply", "-f", "-", stdin=_store(role))
        for kube in (self.app, self.db, self.other):

            def valid(k: Kube = kube) -> bool:
                return _ready(k.get("secretstore", STORE)) == ("True", "Valid")

            _wait(f"the store in {kube.base[-1]} Ready", valid, 120)

    def image_args(self) -> list[str]:
        """The kind job's images (its release's values), as test_tracing_redis.py reads them."""
        cmd = [*self.helm_admin, "get", "values", MAIN_RELEASE, "-n", MAIN_NAMESPACE, "-o", "json"]
        main = json.loads(_run(cmd))
        args: list[str] = []
        for image_id, image in sorted(main.get("images", {}).items()):
            for key in ("registry", "repository", "tag"):
                args += ["--set-string", f"images.{image_id}.{key}={image.get(key, '')}"]
        assert args, f"the release {MAIN_RELEASE} has no image values: run make kind-deploy first"
        return args

    def deploy(self) -> None:
        """helm upgrade --install as the deployer: values.local.yaml and the store values only."""
        store_values = self.work / "store.yaml"
        store_values.write_text(yaml.safe_dump(STORE_VALUES))
        cmd = [*self.helm, "upgrade", "--install", RELEASE, str(CHART), "-n", NAMESPACE]
        cmd += ["-f", str(CHART / "values.local.yaml"), "-f", str(store_values)]
        cmd += [*self.image_args(), "--history-max", "5"]
        cmd += ["--wait", "--wait-for-jobs", "--timeout", "9m"]
        started = time.time()
        out = _run(cmd)
        assert "forbidden" not in out.lower(), out
        print(f"deploy with the OpenBao store: {time.time() - started:.0f}s")

    def secret_data(self, kube: Kube, name: str) -> dict[str, str]:
        """A Secret's decoded data (compared, never printed)."""
        data = kube.get("secret", name).get("data", {})
        return {k: base64.b64decode(v).decode() for k, v in data.items()}

    def remove(self, objects: str) -> None:
        if _run([*self.helm_admin, "status", RELEASE, "-n", NAMESPACE], check=False):
            _run([*self.helm, "uninstall", RELEASE, "-n", NAMESPACE, "--wait"], check=False)
        delete = [*self.cluster_wide, "delete", "--ignore-not-found", "--wait"]
        _run([*delete, "-f", "-"], objects, check=False)
        _run([*delete, "namespace", OTHER, OPENBAO_NAMESPACE], check=False)
        _run([*delete, "clusterrolebinding", TOKEN_REVIEW], check=False)
        helm = [*self.helm_admin, "-n", OPERATOR_NAMESPACE]
        if _run([*helm, "status", OPERATOR_RELEASE], check=False):
            _run([*helm, "uninstall", OPERATOR_RELEASE, "--wait"], check=False)
        _run([*delete, "namespace", OPERATOR_NAMESPACE], check=False)


@pytest.fixture(scope="module")
def setup(request: pytest.FixtureRequest) -> Iterator[Setup]:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up kind-deploy first"
    with tempfile.TemporaryDirectory() as work:
        s = Setup(Path(work))
        boot = ["bash", str(ROOT / "scripts" / "k8s-bootstrap.sh"), ENV]
        objects = _run([*boot, "--release", RELEASE, "--namespace", NAMESPACE])
        s.remove(objects)  # what an interrupted run left behind
        try:
            s.install_operator()
            _run([*s.cluster_wide, "apply", "-f", "-"], objects)
            _run([*s.cluster_wide, "create", "namespace", OTHER])
            s.install_openbao()
            s.stores()
            s.deploy()
            yield s
        finally:
            if request.session.testsfailed:
                for kube in (s.app, s.db, s.other):
                    print(kube("get", "externalsecrets,secretstores", "-o", "yaml", check=False))
                logs = ["logs", "deployment/external-secrets", "--tail=100"]
                print(Kube(OPERATOR_NAMESPACE)(*logs, check=False))
            s.remove(objects)


def test_both_external_secrets_sync_from_the_environments_store(setup: Setup) -> None:
    for kube, name in ((setup.app, f"{RELEASE}-secret"), (setup.db, f"{RELEASE}-db-password")):
        es = kube.get("externalsecret", name)
        assert es["spec"]["secretStoreRef"] == {"name": STORE, "kind": "SecretStore"}, es["spec"]
        assert _ready(es) == ("True", "SecretSynced"), es.get("status")


def test_the_synced_secrets_hold_the_stores_values(setup: Setup) -> None:
    app = setup.secret_data(setup.app, f"{RELEASE}-secret")
    assert set(app) == set(setup.values)
    assert all(app[k] == v for k, v in setup.values.items()), "the app Secret differs from OpenBao"
    db = setup.secret_data(setup.db, f"{RELEASE}-db-password")
    assert set(db) == {"password"}
    assert db["password"] == setup.values["DB_PASSWORD"], "the database's password differs"
    owner = setup.app.get("secret", f"{RELEASE}-secret")["metadata"]["ownerReferences"][0]
    assert owner["kind"] == "ExternalSecret", owner


def test_the_app_pods_start_with_the_value(setup: Setup) -> None:
    """Ready means the backend queried PostgreSQL (its readiness reads the schema) with the synced
    DB_PASSWORD, which the database took from its own ExternalSecret: one value, two syncs."""
    selector = f"app.kubernetes.io/instance={RELEASE},app.kubernetes.io/component=backend"
    pods = setup.app.get("pods", "-l", selector)["items"]
    assert pods, "no backend Pod"
    for pod in pods:
        ready = {c["type"]: c["status"] for c in pod["status"]["conditions"]}
        assert ready.get("Ready") == "True", pod["status"]
        container = pod["spec"]["containers"][0]
        refs = {
            e["valueFrom"]["secretKeyRef"]["name"]
            for e in container.get("env", [])
            if "secretKeyRef" in e.get("valueFrom", {})
        }
        refs |= {f["secretRef"]["name"] for f in container.get("envFrom", []) if "secretRef" in f}
        assert f"{RELEASE}-secret" in refs, refs
    jobs = setup.app.get("jobs", "-l", f"app.kubernetes.io/instance={RELEASE}")["items"]
    assert jobs, "no migration Job"
    assert all(j["status"].get("succeeded") == 1 for j in jobs), [j["status"] for j in jobs]


def test_a_store_of_another_environment_cannot_read_the_path(setup: Setup) -> None:
    """OTHER's store reads its own path, and is refused this environment's."""
    keys = VALUES["secrets"]["externalSecret"]["keys"]

    def external_secret(name: str, env: str) -> str:
        return json.dumps(
            {
                "apiVersion": "external-secrets.io/v1",
                "kind": "ExternalSecret",
                "metadata": {"name": name},
                "spec": {
                    "secretStoreRef": {"name": STORE, "kind": "SecretStore"},
                    "refreshInterval": "1h",
                    "target": {"name": name},
                    "data": [
                        {
                            "secretKey": "value",
                            "remoteRef": {
                                "key": f"{SERVICE}/{env}/{keys['DB_PASSWORD']}",
                                "property": "value",
                            },
                        }
                    ],
                },
            }
        )

    setup.other("apply", "-f", "-", stdin=external_secret("fst-own", "other"))
    setup.other("apply", "-f", "-", stdin=external_secret("fst-theirs", ENV))
    _wait(
        "the other environment's own secret synced",
        lambda: _ready(setup.other.get("externalsecret", "fst-own"))[0] == "True",
        120,
    )
    assert setup.secret_data(setup.other, "fst-own") == {"value": setup.other_value}
    _wait(
        "the refusal",
        lambda: _ready(setup.other.get("externalsecret", "fst-theirs"))[0] == "False",
        120,
    )
    status, reason = _ready(setup.other.get("externalsecret", "fst-theirs"))
    print(f"another environment's store, this environment's path: Ready={status} ({reason})")
    assert reason == "SecretSyncedError", reason
    assert not setup.other.get("secret", "fst-theirs"), "the refused path still made a Secret"
    # The event is generic ("could not get secret data from provider"); the operator's log has
    # OpenBao's answer: the policy refuses the path.
    log = Kube(OPERATOR_NAMESPACE)("logs", "deployment/external-secrets", "--tail=500")
    refused = [ln for ln in log.splitlines() if '"name":"fst-theirs"' in ln and "Code: 403" in ln]
    assert refused, "no 403 from OpenBao for the other environment's read in the operator's log"
