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
only inside its container: never printed, never written to a file. OpenBao's dev server also
writes its root token to its own container log, so no step here may print that log (the failure
dump prints the operator's). OpenBao gets Kubernetes auth with one role per namespace, bound to
that namespace only: the app namespace's policy reads its environment's path, the database
namespace's only its db-password; each namespace gets the store
deploy/external-secrets/secretstore-openbao.yaml (the server and the role replaced, no CA: dev mode
serves http). The release (``RELEASE`` in ``NAMESPACE`` and its database namespace, bootstrapped as
cluster admin, deployed as the bootstrap's deployer with values.local.yaml and the kind job's
images) turns ``secrets.externalSecret`` on with only the store values set to OpenBao. A namespace
of another environment (``OTHER``) has a store of its own, which reads its own path and is refused
the release's; the database namespace's store is refused db-user; and an ExternalSecret naming a
ClusterSecretStore is never synced (the operator runs with processClusterStore false). Everything
is removed at the end; the images come from ghcr.io, and nothing else leaves the runner.
"""

from __future__ import annotations

import base64
import json
import secrets
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.k8s._kube import (
    CONTEXT,
    KUBECONFIG,
    ROOT,
    SH_STDIN_CHECK,
    Kube,
    _run,
    _tool,
    exec_stdin,
    helm_as_deployer,
)
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
CLUSTER_STORE = "fst-cluster-store"
SKIP_WAIT = 30  # seconds a synced ExternalSecret would have needed (a few) to show it
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


def _policy(path: str) -> str:
    """A namespace's policy: read one path (or path glob) of the KV engine, nothing else."""
    return f'path "secret/data/{path}" {{ capabilities = ["read"] }}\n'


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
        target = ["exec", "-i" if stdin is not None else "-q", f"deployment/{OPENBAO}", "--"]
        if stdin is None:
            return self.bao(*target, "sh", "-c", BAO + script, "sh", *args)
        # The container checks the input's digest (the first argument) before the script uses it.
        return exec_stdin(
            self.bao,
            "bao_sh",
            stdin,
            lambda digest: [
                *target,
                "sh",
                "-c",
                SH_STDIN_CHECK + BAO + script,
                "sh",
                digest,
                *args,
            ],
        )

    def install_openbao(self) -> None:
        token = secrets.token_urlsafe(32)
        _run([*self.cluster_wide, "apply", "-f", "-"], _openbao_objects(token))
        self.bao("rollout", "status", f"deployment/{OPENBAO}", "--timeout=180s")
        self.bao_sh(
            "bao auth enable kubernetes >/dev/null && bao write auth/kubernetes/config"
            ' kubernetes_host="https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT"'
            " >/dev/null"
        )
        # One role and one policy per namespace, named for it, bound to that namespace only: the
        # app namespace reads its environment's path, the database namespace only db-password.
        keys = VALUES["secrets"]["externalSecret"]["keys"]
        for namespace, path in (
            (NAMESPACE, f"{SERVICE}/{ENV}/*"),
            (DB_NAMESPACE, f"{SERVICE}/{ENV}/{keys['DB_PASSWORD']}"),
            (OTHER, f"{SERVICE}/other/*"),
        ):
            self.bao_sh('bao policy write "$1" - >/dev/null', namespace, stdin=_policy(path))
            self.bao_sh(
                'bao write "auth/kubernetes/role/$1" bound_service_account_names=external-secrets'
                ' bound_service_account_namespaces="$1" token_policies="$1" audience=openbao'
                " token_ttl=10m >/dev/null",
                namespace,
            )
        # Each value through stdin, never an argument (`value=-`).
        put = 'bao kv put -mount=secret "$1" value=- >/dev/null'
        for key, value in self.values.items():
            self.bao_sh(put, f"{SERVICE}/{ENV}/{keys[key]}", stdin=value)
        self.bao_sh(put, f"{SERVICE}/other/{keys['DB_PASSWORD']}", stdin=self.other_value)

    def stores(self) -> None:
        for kube, role in ((self.app, NAMESPACE), (self.db, DB_NAMESPACE), (self.other, OTHER)):
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

    def deploy(self, values: dict[str, Any] | None = None, what: str = "the OpenBao store") -> None:
        """helm upgrade --install as the deployer: values.local.yaml and the store values only (or
        the given values, in a file of the temporary directory)."""
        store_values = self.work / "store.yaml"
        store_values.write_text(yaml.safe_dump(STORE_VALUES if values is None else values))
        cmd = [
            *self.helm,
            "upgrade",
            "--install",
            RELEASE,
            str(CHART),
            "-n",
            NAMESPACE,
            "--server-side=true",
            "--force-conflicts",
        ]
        cmd += ["-f", str(CHART / "values.local.yaml"), "-f", str(store_values)]
        cmd += [*self.image_args(), "--history-max", "5"]
        cmd += ["--wait", "--wait-for-jobs", "--timeout", "9m"]
        started = time.time()
        out = _run(cmd)
        assert "forbidden" not in out.lower(), out
        print(f"deploy with {what}: {time.time() - started:.0f}s")

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
        _run([*delete, "clustersecretstore", CLUSTER_STORE], check=False)
        helm = [*self.helm_admin, "-n", OPERATOR_NAMESPACE]
        if _run([*helm, "status", OPERATOR_RELEASE], check=False):
            _run([*helm, "uninstall", OPERATOR_RELEASE, "--wait"], check=False)
        _run([*delete, "namespace", OPERATOR_NAMESPACE], check=False)


@contextmanager
def environment(
    request: pytest.FixtureRequest, before: Callable[[Setup], None] | None = None
) -> Iterator[Setup]:
    """Everything set up and the release deployed with the store values; `before` runs first,
    once the stores are Ready (test_external_secrets_switch.py deploys the chart's own Secret)."""
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
            if before is not None:
                before(s)
            s.deploy()
            yield s
        finally:
            if request.session.testsfailed:
                for kube in (s.app, s.db, s.other):
                    print(kube("get", "externalsecrets,secretstores", "-o", "yaml", check=False))
                logs = ["logs", "deployment/external-secrets", "--tail=100"]
                print(Kube(OPERATOR_NAMESPACE)(*logs, check=False))
            s.remove(objects)


@pytest.fixture(scope="module")
def setup(request: pytest.FixtureRequest) -> Iterator[Setup]:
    with environment(request) as s:
        yield s


def test_both_external_secrets_sync_from_the_environments_store(setup: Setup) -> None:
    for kube, name in ((setup.app, f"{RELEASE}-secret"), (setup.db, f"{RELEASE}-db-password")):
        es = kube.get("externalsecret", name)
        assert es["spec"]["secretStoreRef"] == {"name": STORE, "kind": "SecretStore"}, es["spec"]
        assert _ready(es) == ("True", "SecretSynced"), es.get("status")


def test_the_synced_secrets_hold_the_stores_values(setup: Setup) -> None:
    # Each comparison first, then a boolean: a failed comparison in an assert prints both sides.
    app = setup.secret_data(setup.app, f"{RELEASE}-secret")
    same_keys = sorted(app) == sorted(setup.values)
    assert same_keys, f"the app Secret's keys are {sorted(app)}"
    same = all(app[k] == v for k, v in setup.values.items())
    assert same, "the app Secret differs from OpenBao"
    db = setup.secret_data(setup.db, f"{RELEASE}-db-password")
    same_keys = sorted(db) == ["password"]
    assert same_keys, f"the database's Secret's keys are {sorted(db)}"
    same = db["password"] == setup.values["DB_PASSWORD"]
    assert same, "the database's password differs from the app's"
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


def _external_secret(name: str, key: str, kind: str = "SecretStore", store: str = STORE) -> str:
    """An ExternalSecret of one key (property value) from the named store, as JSON."""
    ref = {"key": key, "property": "value"}
    spec = {
        "secretStoreRef": {"name": store, "kind": kind},
        "refreshInterval": "1h",
        "target": {"name": name},
        "data": [{"secretKey": "value", "remoteRef": ref}],
    }
    obj = {"apiVersion": "external-secrets.io/v1", "kind": "ExternalSecret"}
    return json.dumps({**obj, "metadata": {"name": name}, "spec": spec})


def _refused(kube: Kube, name: str, what: str) -> None:
    """The ExternalSecret ``name`` fails to sync, makes no Secret, and the operator's log has
    OpenBao's 403 for it (the event is generic: "could not get secret data from provider")."""
    _wait(
        f"the refusal of {what}",
        lambda: _ready(kube.get("externalsecret", name))[0] == "False",
        120,
    )
    status, reason = _ready({"status": _status(kube, "externalsecret", name)})
    print(f"{what}: Ready={status} ({reason})")
    assert reason == "SecretSyncedError", reason
    made = bool(kube.get("secret", name))  # a boolean: a failed assert never prints the Secret
    assert not made, f"the refused read of {what} still made a Secret"
    log = Kube(OPERATOR_NAMESPACE)("logs", "deployment/external-secrets", "--tail=1000")
    namespace = kube.base[-1]
    refused = [
        ln
        for ln in log.splitlines()
        if f'"name":"{name}","namespace":"{namespace}"' in ln and "Code: 403" in ln
    ]
    assert refused, f"no 403 from OpenBao for {what} in the operator's log"


def _status(kube: Kube, kind: str, name: str) -> dict[str, Any]:
    """The object's ``.status`` and nothing else (kubectl's own filter), ``{}`` if it has none."""
    out = kube("get", kind, name, "--ignore-not-found", "-o", "jsonpath={.status}")
    return json.loads(out) if out else {}


def _sync_diagnostics(kube: Kube, name: str) -> str:
    """Why a Secret differs, read without its data: kubectl prints only its resourceVersion and
    key names, plus the ExternalSecret's refreshTime, syncedResourceVersion and conditions."""
    version = kube(
        "get", "secret", name, "--ignore-not-found", "-o", "jsonpath={.metadata.resourceVersion}"
    )
    fields = kube(
        "get",
        "secret",
        name,
        "--ignore-not-found",
        "-o",
        "go-template={{range $k, $v := .data}}{{$k}} {{end}}",
    )
    es = _status(kube, "externalsecret", name)
    return (
        f"exists={bool(version)} resourceVersion={version or None} fields={sorted(fields.split())}"
        f" refreshTime={es.get('refreshTime')}"
        f" syncedResourceVersion={es.get('syncedResourceVersion')}"
        f" conditions={es.get('conditions')}"
    )


def _wait_secret_data(setup: Setup, kube: Kube, name: str, want: dict[str, str]) -> None:
    """The Secret's data becomes ``want`` within 120s. The ExternalSecret's Ready is no proof of the
    Secret's content at that instant (one CI run read a Secret that differed from OpenBao just after
    Ready), so poll the content itself; a Secret that never matches fails with the diagnostics,
    taken at the first mismatch too, when they still show the difference."""
    polls = 0
    first = ""

    def matches() -> bool:
        nonlocal polls, first
        polls += 1
        if setup.secret_data(kube, name) == want:  # a boolean: nothing is printed
            return True
        if not first:
            first = _sync_diagnostics(kube, name)
        return False

    end = time.time() + 120
    while not matches():
        if time.time() > end:
            pytest.fail(
                f"{name} differs from OpenBao after 120s; at the first poll: {first};"
                f" now: {_sync_diagnostics(kube, name)}"
            )
        time.sleep(1)
    if polls > 1:
        print(f"{name} matched OpenBao only at poll {polls}; at the first poll: {first}")


def test_a_store_of_another_environment_cannot_read_the_path(setup: Setup) -> None:
    """OTHER's store reads its own path, and is refused this environment's."""
    keys = VALUES["secrets"]["externalSecret"]["keys"]
    own = _external_secret("fst-own", f"{SERVICE}/other/{keys['DB_PASSWORD']}")
    setup.other("apply", "-f", "-", stdin=own)
    theirs = _external_secret("fst-theirs", f"{SERVICE}/{ENV}/{keys['DB_PASSWORD']}")
    setup.other("apply", "-f", "-", stdin=theirs)
    _wait(
        "the other environment's own secret synced",
        lambda: _ready(setup.other.get("externalsecret", "fst-own"))[0] == "True",
        120,
    )
    _wait_secret_data(setup, setup.other, "fst-own", {"value": setup.other_value})
    leaked = setup.other_value in _sync_diagnostics(setup.other, "fst-own")
    assert not leaked, "the diagnostics hold the Secret's value"
    _refused(setup.other, "fst-theirs", "another environment's store, this environment's path")


def test_the_database_namespaces_store_reads_only_the_database_password(setup: Setup) -> None:
    """The database namespace's role reads db-password (the release's own ExternalSecret above)
    and is refused every other key of its environment."""
    keys = VALUES["secrets"]["externalSecret"]["keys"]
    user = _external_secret("fst-db-user", f"{SERVICE}/{ENV}/{keys['DB_USER']}")
    setup.db("apply", "-f", "-", stdin=user)
    _refused(setup.db, "fst-db-user", "the database namespace's store, the app's db-user")


def test_an_external_secret_naming_a_cluster_store_is_never_synced(setup: Setup) -> None:
    """The operator runs with processClusterStore false (the guide's values): an ExternalSecret a
    deployer points at a ClusterSecretStore is skipped, even at a store that could read the path."""
    keys = VALUES["secrets"]["externalSecret"]["keys"]
    store = yaml.safe_load(_store(NAMESPACE))["items"][1]
    store["kind"] = "ClusterSecretStore"
    store["metadata"] = {"name": CLUSTER_STORE}
    store["spec"]["provider"]["vault"]["auth"]["kubernetes"]["serviceAccountRef"]["namespace"] = (
        NAMESPACE
    )
    _run([*setup.cluster_wide, "apply", "-f", "-"], json.dumps(store))
    via = _external_secret(
        "fst-via-cluster", f"{SERVICE}/{ENV}/{keys['DB_USER']}", "ClusterSecretStore", CLUSTER_STORE
    )
    setup.other("apply", "-f", "-", stdin=via)
    time.sleep(SKIP_WAIT)  # a skipped ExternalSecret gets no status at all: wait, then look
    status = _status(setup.other, "externalsecret", "fst-via-cluster")
    print(f"an ExternalSecret naming a ClusterSecretStore: status {status}")
    assert _ready({"status": status})[0] != "True", status
    made = bool(setup.other.get("secret", "fst-via-cluster"))
    assert not made, "an ExternalSecret naming a ClusterSecretStore made its Secret"
