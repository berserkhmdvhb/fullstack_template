# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The one-time admin bootstrap (scripts/k8s-bootstrap.sh, infra/k8s/bootstrap); the chart's side.

The chart creates no namespace by default (namespaces.create: false) and carries no ResourceQuota or
LimitRange. The bootstrap creates the namespaces on the restricted Pod Security profile, the quota
and the LimitRange, and a least-privilege deployer Role per namespace. The deployer's resource set
is derived from the chart here, so a kind added to the chart fails this test until the Role has it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.backend.test_chart_monitoring import APP_GROUPS
from tests.backend.test_chart_pod_security import ALL_ON, VALUES_FILES
from tests.backend.test_chart_production_guard import CHART, ROOT, find_helm, require_helm

SCRIPT = ROOT / "scripts" / "k8s-bootstrap.sh"
BOOTSTRAP = ROOT / "infra" / "k8s" / "bootstrap"
ENVS = ["dev", "qa", "production", "local"]
PSS_LABELS = {
    f"pod-security.kubernetes.io/{mode}{suffix}": value
    for mode in ("enforce", "audit", "warn")
    for suffix, value in (("", "restricted"), ("-version", "v1.34"))
}
HELM_VERBS = {"get", "list", "watch", "create", "update", "patch", "delete"}
BOOTSTRAP_NAME = "bootstrap-deployer"

# Kind -> (apiGroup, resource). A kind the chart renders that is missing here fails the test: add it
# here AND to the deployer Role (infra/k8s/bootstrap/templates/deployer-rbac.yaml).
KIND_TO_RESOURCE = {
    "ConfigMap": ("", "configmaps"),
    "Secret": ("", "secrets"),
    "Service": ("", "services"),
    "ServiceAccount": ("", "serviceaccounts"),
    "PersistentVolumeClaim": ("", "persistentvolumeclaims"),
    "Deployment": ("apps", "deployments"),
    "StatefulSet": ("apps", "statefulsets"),
    "Job": ("batch", "jobs"),
    "CronJob": ("batch", "cronjobs"),
    "HorizontalPodAutoscaler": ("autoscaling", "horizontalpodautoscalers"),
    "PodDisruptionBudget": ("policy", "poddisruptionbudgets"),
    "NetworkPolicy": ("networking.k8s.io", "networkpolicies"),
    "Ingress": ("networking.k8s.io", "ingresses"),
    "HTTPRoute": ("gateway.networking.k8s.io", "httproutes"),
    "HealthCheckPolicy": ("networking.gke.io", "healthcheckpolicies"),
    "GCPBackendPolicy": ("networking.gke.io", "gcpbackendpolicies"),
    "ServiceMonitor": ("monitoring.coreos.com", "servicemonitors"),
    "PodMonitoring": ("monitoring.googleapis.com", "podmonitorings"),
    "Rules": ("monitoring.googleapis.com", "rules"),
    "PrometheusRule": ("monitoring.coreos.com", "prometheusrules"),
    "ExternalSecret": ("external-secrets.io", "externalsecrets"),
    "Role": ("rbac.authorization.k8s.io", "roles"),
    "RoleBinding": ("rbac.authorization.k8s.io", "rolebindings"),
}
# Helm stores each release revision as a Secret in the release namespace.
HELM_RELEASE_SECRETS = {("", "secrets")}
# The deploy-ops rights beyond the chart's kinds, each with its user. Pinned exactly, and no exec.
DEPLOY_CHECKS = {
    "app": {
        # helm --wait reads a Deployment's ReplicaSets and Pods; deploy-verify.sh lists Pods.
        ("", "pods"): {"get", "list", "watch"},
        ("apps", "replicasets"): {"get", "list", "watch"},
        # migration-job-lib.sh prints the migration Job's logs on a failed deploy.
        ("", "pods/log"): {"get"},
        # deploy-env.sh's smoke test and deploy-verify.sh's health checks port-forward.
        ("", "pods/portforward"): {"create"},
    },
    # helm --wait reads a StatefulSet's Pods.
    "db": {("", "pods"): {"get", "list", "watch"}},
}
DEPLOY_ENV = ROOT / "scripts" / "ci" / "deploy-env.sh"
DEPLOY_OPS = ROOT / ".github" / "workflows" / "deploy-ops.yml"
K8S_ENV = ROOT / "scripts" / "k8s-env.sh"
# Secrets in the app namespace that neither Helm nor the chart creates: an image pull secret, an
# edge TLS secret and a database CA secret (wiki/gke-cluster-setup.md, values.yaml).
SECRET_HEADROOM = 3


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def _docs(text: str) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(text) if d]


def chart(helm: str, *args: str, release: str = "svc", ns: str = "svc-dev") -> list[dict[str, Any]]:
    result = subprocess.run(
        [helm, "template", release, str(CHART), "-n", ns, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return _docs(result.stdout)


def run_bootstrap(helm: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{Path(helm).parent}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", str(SCRIPT), *args],  # noqa: S607 - bash from PATH, as an admin runs it
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def bootstrap(helm: str, *args: str) -> list[dict[str, Any]]:
    result = run_bootstrap(helm, *args)
    assert result.returncode == 0, result.stderr
    return _docs(result.stdout)


def _one(docs: list[dict[str, Any]], kind: str, ns: str | None = None) -> dict[str, Any]:
    found = [
        d
        for d in docs
        if d["kind"] == kind and (ns is None or d["metadata"].get("namespace") == ns)
    ]
    assert len(found) == 1, (kind, ns, found)
    return found[0]


# ---- The chart ---------------------------------------------------------------------------------


@pytest.mark.parametrize("values", VALUES_FILES)
def test_the_chart_creates_no_namespace_quota_or_limitrange_by_default(
    helm: str, values: str
) -> None:
    kinds = {d["kind"] for d in chart(helm, "-f", str(CHART / values), *ALL_ON)}
    assert not kinds & {"Namespace", "ResourceQuota", "LimitRange"}


def test_namespaces_create_true_renders_both_namespaces_restricted_and_kept(helm: str) -> None:
    docs = chart(helm, "-f", str(CHART / "values.dev.yaml"), "--set", "namespaces.create=true")
    namespaces = {d["metadata"]["name"]: d["metadata"] for d in docs if d["kind"] == "Namespace"}
    assert set(namespaces) == {"svc-dev", "svc-dev-db"}
    for meta in namespaces.values():
        assert PSS_LABELS.items() <= meta["labels"].items()
        # Switching create off later must not make Helm delete a namespace and everything in it.
        assert meta["annotations"]["helm.sh/resource-policy"] == "keep"


def test_values_and_schema_carry_no_quota_or_limitrange_and_create_defaults_false() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    schema = json.loads((CHART / "values.schema.json").read_text())
    assert "quota" not in values
    assert "limitRange" not in values
    assert not {"quota", "limitRange"} & set(schema["properties"])
    assert values["namespaces"]["create"] is False
    assert schema["properties"]["namespaces"]["properties"]["create"] == {"type": "boolean"}
    assert not list(CHART.glob("templates/resourcequota.yaml"))


# ---- The bootstrap render ----------------------------------------------------------------------


@pytest.mark.parametrize("env", ENVS)
def test_the_bootstrap_renders_restricted_namespaces(helm: str, env: str) -> None:
    docs = bootstrap(helm, env)
    namespaces = {
        d["metadata"]["name"]: d["metadata"]["labels"] for d in docs if d["kind"] == "Namespace"
    }
    app = f"your-service-name-{env}"
    in_cluster_db = env != "production"
    assert set(namespaces) == ({app, f"{app}-db"} if in_cluster_db else {app})
    for labels in namespaces.values():
        assert PSS_LABELS.items() <= labels.items()
        assert labels["environment"] == env
    # db-network-policy.yaml selects the app namespace by this label.
    assert namespaces[app]["tier"] == "app-ns"
    if in_cluster_db:
        assert namespaces[f"{app}-db"]["tier"] == "db"
    for d in docs:
        annotations = d["metadata"].get("annotations") or {}
        assert not [a for a in annotations if a.startswith("meta.helm.sh/")], (
            "the admin applies it; no Helm release owns it"
        )


def test_the_db_namespace_follows_the_flags(helm: str) -> None:
    names = {
        d["metadata"]["name"]
        for d in bootstrap(helm, "production", "--db")
        if d["kind"] == "Namespace"
    }
    assert names == {"your-service-name-production", "your-service-name-production-db"}
    docs = bootstrap(helm, "dev", "--no-db", "--release", "acme", "--namespace", "team-a")
    assert {d["metadata"]["namespace"] for d in docs if "namespace" in d["metadata"]} == {"team-a"}
    assert [d["metadata"]["name"] for d in docs if d["kind"] == "Namespace"] == ["team-a"]


@pytest.mark.parametrize("args", [(), ("staging",), ("dev", "--bogus"), ("dev", "--release")])
def test_the_script_refuses_bad_arguments(helm: str, args: tuple[str, ...]) -> None:
    result = run_bootstrap(helm, *args)
    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize("env", ENVS)
def test_the_quota_and_limitrange_keep_the_chart_defaults_without_cpu_limits(
    helm: str, env: str
) -> None:
    docs = bootstrap(helm, env)
    app = f"your-service-name-{env}"
    quota = _one(docs, "ResourceQuota", app)["spec"]["hard"]
    assert quota == {
        "requests.cpu": "6",
        "requests.memory": "27Gi",
        "limits.memory": "27Gi",
        "pods": "30",
        "services": "10",
        "persistentvolumeclaims": "5",
        "configmaps": "10",
        "secrets": "15",
    }
    limits = _one(docs, "LimitRange", app)["spec"]["limits"]
    container = next(item for item in limits if item["type"] == "Container")
    pod = next(item for item in limits if item["type"] == "Pod")
    # A LimitRange max with no default becomes the default limit, so CPU appears in neither.
    assert container["default"] == {"memory": "512Mi"}
    assert container["defaultRequest"] == {"cpu": "100m", "memory": "128Mi"}
    assert container["max"] == {"memory": "2Gi"}
    assert container["min"] == {"cpu": "50m", "memory": "64Mi"}
    assert pod["max"] == {"memory": "4Gi"}
    assert not [
        d
        for d in docs
        if d["kind"] in {"ResourceQuota", "LimitRange"} and d["metadata"]["namespace"] != app
    ]


# ---- The deployer RBAC -------------------------------------------------------------------------


def _rules(role: dict[str, Any]) -> dict[tuple[str, str], set[str]]:
    """(apiGroup, resource) -> verbs. The deployer's rules never name resources."""
    out: dict[tuple[str, str], set[str]] = {}
    for rule in role["rules"]:
        assert not rule.get("resourceNames"), rule
        for group in rule["apiGroups"]:
            for res in rule["resources"]:
                out.setdefault((group, res), set()).update(rule["verbs"])
    return out


def _deployer(docs: list[dict[str, Any]], ns: str) -> dict[str, Any]:
    roles = [
        d
        for d in docs
        if d["kind"] == "Role"
        and d["metadata"]["name"] == BOOTSTRAP_NAME
        and d["metadata"]["namespace"] == ns
    ]
    assert len(roles) == 1, ns
    return roles[0]


def _merged(*files: Path) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in files:
        _deep_merge(out, yaml.safe_load(f.read_text()) or {})
    return out


def _deep_merge(into: dict[str, Any], top: dict[str, Any]) -> None:
    for k, v in top.items():
        if isinstance(v, dict) and isinstance(into.get(k), dict):
            _deep_merge(into[k], v)
        else:
            into[k] = v


def chart_resources(helm: str, release: str, ns: str) -> dict[str, set[tuple[str, str]]]:
    """The (apiGroup, resource) set the chart renders per namespace, in every values file/option."""
    out: dict[str, set[tuple[str, str]]] = {"app": set(), "db": set()}
    for values in VALUES_FILES:
        for args in (
            (),
            ALL_ON,
            # Google Managed Prometheus: a PodMonitoring and a namespaced Rules object.
            (*ALL_ON, "--set", "monitoring.flavour=gmp"),
            (
                *ALL_ON,
                "--set",
                "edge.mode=ingress",
                "--set-json",
                'edge.namespaceSelector={"kubernetes.io/metadata.name":"x"}',
            ),
        ):
            for d in chart(helm, "-f", str(CHART / values), *args, release=release, ns=ns):
                kind = d["kind"]
                assert kind in KIND_TO_RESOURCE, (
                    f"{kind}: add it to KIND_TO_RESOURCE and the deployer Role"
                )
                group, resource = KIND_TO_RESOURCE[kind]
                api_group = d["apiVersion"].rpartition("/")[0]
                assert api_group == group, (kind, d["apiVersion"])
                where = {ns: "app", f"{ns}-db": "db"}[d["metadata"]["namespace"]]
                out[where].add((group, resource))
    return out


@pytest.mark.parametrize("release", ["your-service-name", "svc"])
def test_the_deployer_role_covers_exactly_what_the_chart_renders(helm: str, release: str) -> None:
    ns = f"{release}-dev"
    derived = chart_resources(helm, release, ns)
    assert {
        "HTTPRoute",
        "Ingress",
        "HealthCheckPolicy",
        "ServiceMonitor",
        "PodMonitoring",
        "Rules",
        "StatefulSet",
        "CronJob",
    } <= {k for k, v in KIND_TO_RESOURCE.items() if v in derived["app"] | derived["db"]}
    # The app's Role and RoleBinding come from the bootstrap (rbac.create false), never the deploy.
    for found in derived.values():
        assert not [k for k in found if k[0] == "rbac.authorization.k8s.io"], (
            "the chart rendered RBAC by default: gate it on rbac.create"
        )
    docs = bootstrap(helm, "dev", "--release", release)
    for where, namespace in (("app", ns), ("db", f"{ns}-db")):
        managed = derived[where] | (HELM_RELEASE_SECRETS if where == "app" else set())
        expected = {k: set(HELM_VERBS) for k in managed}
        for key, verbs in DEPLOY_CHECKS[where].items():
            expected[key] = expected.get(key, set()) | verbs
        assert _rules(_deployer(docs, namespace)) == expected, where


@pytest.mark.parametrize("env", ENVS)
def test_the_deployer_never_gets_wildcards_escalation_rbac_or_namespace_rights(
    helm: str, env: str
) -> None:
    docs = bootstrap(helm, env)
    roles = [d for d in docs if d["kind"] == "Role" and d["metadata"]["name"] == BOOTSTRAP_NAME]
    assert len(roles) == len([d for d in docs if d["kind"] == "Namespace"])
    for role in roles:
        for rule in role["rules"]:
            assert "*" not in rule["verbs"] + rule["resources"] + rule["apiGroups"]
            assert not {"escalate", "bind", "impersonate"} & set(rule["verbs"])
            assert not {"namespaces", "resourcequotas", "limitranges"} & set(rule["resources"])
            assert "rbac.authorization.k8s.io" not in rule["apiGroups"]
    # The chart never uses the bootstrap's names.
    for values in VALUES_FILES:
        names = {d["metadata"]["name"] for d in chart(helm, "-f", str(CHART / values), *ALL_ON)}
        assert BOOTSTRAP_NAME not in names


def _norm(doc: dict[str, Any]) -> dict[str, Any]:
    """A manifest without what only says where it came from."""
    meta = {k: v for k, v in doc["metadata"].items() if k != "annotations"}
    return {**doc, "metadata": meta}


@pytest.mark.parametrize("env", ENVS)
def test_the_bootstraps_app_rbac_is_the_charts_own_render(helm: str, env: str) -> None:
    docs = bootstrap(helm, env)
    app = f"your-service-name-{env}"
    result = subprocess.run(
        [
            helm,
            "template",
            "your-service-name",
            str(CHART),
            "-n",
            app,
            "-f",
            str(CHART / f"values.{env}.yaml"),
            "--set",
            "rbac.create=true",
            "--show-only",
            "templates/rbac.yaml",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    expected = sorted((_norm(d) for d in _docs(result.stdout)), key=lambda d: d["kind"])
    assert [d["kind"] for d in expected] == ["Role", "RoleBinding"]
    got = sorted(
        (
            _norm(d)
            for d in docs
            if d["kind"] in {"Role", "RoleBinding"} and d["metadata"]["name"] != BOOTSTRAP_NAME
        ),
        key=lambda d: d["kind"],
    )
    assert got == expected


@pytest.mark.parametrize("values", VALUES_FILES)
def test_with_rbac_create_false_the_chart_renders_no_role(helm: str, values: str) -> None:
    kinds = {d["kind"] for d in chart(helm, "-f", str(CHART / values), *ALL_ON)}
    assert not kinds & {"Role", "RoleBinding"}
    on = chart(helm, "-f", str(CHART / values), "--set", "rbac.create=true")
    assert {"Role", "RoleBinding"} <= {d["kind"] for d in on}
    schema = json.loads((CHART / "values.schema.json").read_text())
    assert schema["properties"]["rbac"]["properties"]["create"] == {"type": "boolean"}
    assert yaml.safe_load((CHART / "values.yaml").read_text())["rbac"]["create"] is False


@pytest.mark.parametrize("env", ENVS)
def test_each_namespace_binds_the_configured_deployer(helm: str, env: str) -> None:
    docs = bootstrap(helm, env)
    config = _merged(BOOTSTRAP / "values.yaml", BOOTSTRAP / f"values.{env}.yaml")["deployer"]
    bindings = [
        d for d in docs if d["kind"] == "RoleBinding" and d["metadata"]["name"] == BOOTSTRAP_NAME
    ]
    assert len(bindings) == len([d for d in docs if d["kind"] == "Namespace"])
    for b in bindings:
        assert b["roleRef"] == {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": BOOTSTRAP_NAME,
        }
        assert b["subjects"] == [
            {"apiGroup": "rbac.authorization.k8s.io", "kind": "User", "name": config["name"]}
        ]
    # The committed default is a placeholder, never a real identity.
    assert config["name"].startswith("your-deployer")


def test_a_service_account_subject_carries_its_namespace(helm: str) -> None:
    sets = ("deployer.kind=ServiceAccount", "deployer.name=ci", "deployer.namespace=ops")
    result = subprocess.run(
        [
            helm,
            "template",
            "bootstrap",
            str(BOOTSTRAP),
            "-f",
            str(BOOTSTRAP / "values.dev.yaml"),
            *(a for s in sets for a in ("--set", s)),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for b in (d for d in _docs(result.stdout) if d["kind"] == "RoleBinding"):
        assert b["subjects"] == [{"kind": "ServiceAccount", "name": "ci", "namespace": "ops"}]


@pytest.mark.parametrize("env", ENVS)
def test_the_deploy_ops_rights_hold_no_exec(helm: str, env: str) -> None:
    for rights in DEPLOY_CHECKS.values():
        assert not [r for _, r in rights if "exec" in r or "attach" in r]
    for role in (d for d in bootstrap(helm, env) if d["kind"] == "Role"):
        resources = {r for rule in role["rules"] for r in rule["resources"]}
        assert not {"pods/exec", "pods/attach"} & resources


def _history_max(text: str, pattern: str) -> set[int]:
    found = {int(m) for m in re.findall(pattern, text)}
    assert found, pattern
    return found


@pytest.mark.parametrize("env", ENVS)
def test_the_secrets_quota_covers_the_helm_history_the_chart_and_headroom(
    helm: str, env: str
) -> None:
    # Every helm upgrade and rollback, in CI and locally, passes one explicit --history-max.
    script, ops, local = DEPLOY_ENV.read_text(), DEPLOY_OPS.read_text(), K8S_ENV.read_text()
    for text, calls in (
        (script, ("helm upgrade", "helm rollback")),
        (ops, ("helm rollback",)),
        (local, ("local -a helm=(upgrade",)),
    ):
        joined = text.replace("\\\n", " ")
        lines = [ln for ln in joined.splitlines() if ln.lstrip().startswith(calls)]
        assert lines, calls
        for line in lines:
            assert "--history-max" in line, line
    history = (
        _history_max(script, r"HELM_HISTORY_MAX=(\d+)")
        | _history_max(local, r"HELM_HISTORY_MAX=(\d+)")
        | _history_max(ops, r"--history-max (\d+)")
    )
    assert len(history) == 1, history
    (history_max,) = history
    app = f"your-service-name-{env}"
    chart_secrets = max(
        sum(1 for d in docs if d["kind"] == "Secret" and d["metadata"]["namespace"] == app)
        for docs in (
            chart(helm, "-f", str(CHART / values), *args, release="your-service-name", ns=app)
            for values in VALUES_FILES
            for args in ((), ALL_ON)
        )
    )
    quota = int(_one(bootstrap(helm, env), "ResourceQuota")["spec"]["hard"]["secrets"])
    # One more release Secret exists while an upgrade runs, before Helm prunes the oldest.
    assert quota >= history_max + 1 + chart_secrets + SECRET_HEADROOM


def _extra(tmp_path: Path, config: dict[str, Any]) -> str:
    f = tmp_path / "extra.yaml"
    f.write_text(yaml.safe_dump(config))
    return str(f)


@pytest.mark.parametrize(
    ("config", "chart_sets", "names"),
    [
        (
            {"fullname": "acme-api"},
            ("fullnameOverride=acme-api",),
            {"acme-api-role", "acme-api-binding"},
        ),
        ({"nameOverride": "api"}, ("nameOverride=api",), {"svc-api-role", "svc-api-binding"}),
        ({}, (), {"svc-your-service-name-role", "svc-your-service-name-binding"}),
    ],
)
def test_the_bootstrap_names_the_charts_rbac_like_the_chart(
    helm: str, tmp_path: Path, config: dict[str, Any], chart_sets: tuple[str, ...], names: set[str]
) -> None:
    docs = bootstrap(helm, "dev", "--release", "svc", "--values", _extra(tmp_path, config))
    got = {
        d["metadata"]["name"]
        for d in docs
        if d["kind"] in {"Role", "RoleBinding"} and d["metadata"]["name"] != BOOTSTRAP_NAME
    }
    sets = [a for s in ("rbac.create=true", *chart_sets) for a in ("--set", s)]
    rendered = chart(helm, "-f", str(CHART / "values.dev.yaml"), *sets)
    assert (
        got
        == names
        == {d["metadata"]["name"] for d in rendered if d["kind"] in {"Role", "RoleBinding"}}
    )
    values = yaml.safe_load((BOOTSTRAP / "values.yaml").read_text())
    assert values["fullname"] == ""
    assert values["nameOverride"] == ""


@pytest.mark.parametrize("env", ENVS)
def test_the_db_namespace_follows_the_charts_in_cluster_database(env: str) -> None:
    boot = _merged(BOOTSTRAP / "values.yaml", BOOTSTRAP / f"values.{env}.yaml")
    app = _merged(CHART / "values.yaml", CHART / f"values.{env}.yaml")
    assert boot["database"]["inCluster"] == app["postgresql"]["enabled"], env


@pytest.mark.parametrize(
    ("config", "subject"), [({"serviceAccountName": "app-sa"}, "app-sa"), ({}, None)]
)
def test_the_app_rolebinding_names_the_configured_service_account(
    helm: str, tmp_path: Path, config: dict[str, Any], subject: str | None
) -> None:
    docs = bootstrap(helm, "dev", "--values", _extra(tmp_path, config))
    binding = next(
        d for d in docs if d["kind"] == "RoleBinding" and d["metadata"]["name"] != BOOTSTRAP_NAME
    )
    # Unset, the chart's own serviceAccount.name (values.yaml) is used, as the chart does.
    chart_sa = _merged(CHART / "values.yaml", CHART / "values.dev.yaml")["serviceAccount"]["name"]
    sa = subject or chart_sa
    assert binding["subjects"] == [
        {"kind": "ServiceAccount", "name": sa, "namespace": "your-service-name-dev"}
    ]
    sets = ("--set", f"serviceAccount.name={sa}", "--set", "rbac.create=true")
    rendered = chart(
        helm,
        "-f",
        str(CHART / "values.dev.yaml"),
        *sets,
        release="your-service-name",
        ns="your-service-name-dev",
    )
    assert (
        binding["subjects"] == next(d for d in rendered if d["kind"] == "RoleBinding")["subjects"]
    )
    assert yaml.safe_load((BOOTSTRAP / "values.yaml").read_text())["serviceAccountName"] == ""


# ---- GMP ClusterRules: cluster-scoped, so the admin applies them -------------------------------


def test_the_bootstrap_renders_no_clusterrules_by_default(helm: str) -> None:
    assert not [d for d in bootstrap(helm, "dev") if d["kind"] == "ClusterRules"]
    assert "gmpClusterRules: false" in (BOOTSTRAP / "values.yaml").read_text(encoding="utf-8")


def _gmp_args(tmp_path: Path, *, flavour: str = "gmp") -> list[str]:
    """gmpClusterRules on, and the environment's chart flavour (a layered chart values file)."""
    boot = tmp_path / "boot.yaml"
    boot.write_text("gmpClusterRules: true\n", encoding="utf-8")
    chart_values = tmp_path / "chart.yaml"
    chart_values.write_text(f"monitoring:\n  flavour: {flavour}\n", encoding="utf-8")
    return ["--values", str(boot), "--chart-values", str(chart_values)]


def _rule_set(
    groups: list[dict[str, Any]], *, label: str = "namespace"
) -> dict[tuple[str, str], dict[str, Any]]:
    """Every rule by group and name, whole (alert, expr, for, labels, annotations), with the one
    configurable difference, the object-namespace label, put back to `namespace`."""
    return {
        (g["name"], r["alert"]): {
            **r,
            "expr": r["expr"].replace(f'{label}="', 'namespace="'),
        }
        for g in groups
        for r in g["rules"]
    }


def test_the_bootstraps_clusterrules_are_the_operators_kube_rules_rule_by_rule(
    helm: str, tmp_path: Path
) -> None:
    """One source: nothing but the namespace label differs between the flavours' cluster rules."""
    cluster = _one(bootstrap(helm, "dev", *_gmp_args(tmp_path)), "ClusterRules")
    operator = _one(
        chart(
            helm,
            "-f",
            str(CHART / "values.dev.yaml"),
            "--set",
            "monitoring.prometheusRule=true",
            release="your-service-name",
            ns="your-service-name-dev",
        ),
        "PrometheusRule",
    )
    gmp_rules = _rule_set(cluster["spec"]["groups"], label="exported_namespace")
    operator_rules = _rule_set(
        [g for g in operator["spec"]["groups"] if g["name"] not in APP_GROUPS]
    )
    assert gmp_rules
    assert gmp_rules == operator_rules


def test_gmp_cluster_rules_with_the_operator_flavour_fail_clearly(
    helm: str, tmp_path: Path
) -> None:
    result = run_bootstrap(helm, "dev", *_gmp_args(tmp_path, flavour="prometheus-operator"))
    assert result.returncode != 0
    assert "gmpClusterRules" in result.stderr
    assert "prometheus-operator" in result.stderr
    assert "ClusterRules" not in result.stdout


def test_gmp_cluster_rules_with_no_gmp_flavour_anywhere_fail_clearly(
    helm: str, tmp_path: Path
) -> None:
    """The environment's chart files default to the operator flavour: refused, not forced."""
    boot = tmp_path / "boot.yaml"
    boot.write_text("gmpClusterRules: true\n", encoding="utf-8")
    result = run_bootstrap(helm, "dev", "--values", str(boot))
    assert result.returncode != 0
    assert "monitoring.flavour" in result.stderr


def test_production_clusterrules_with_the_placeholder_runbook_url_are_refused(
    helm: str, tmp_path: Path
) -> None:
    """The ClusterRules carry every cluster alert's runbook link, and the bootstrap applies them
    without the deploy: production refuses the placeholder here too."""
    result = run_bootstrap(helm, "production", *_gmp_args(tmp_path))
    assert result.returncode != 0
    assert "monitoring.runbookUrl" in result.stderr
    assert "ClusterRules" not in result.stdout


def test_production_clusterrules_with_a_real_runbook_url_render(helm: str, tmp_path: Path) -> None:
    url = "https://git.svc.test/ops/blob/main/wiki/operations-runbook.md"
    args = (*_gmp_args(tmp_path), "--chart-set", f"monitoring.runbookUrl={url}")
    rules = _one(bootstrap(helm, "production", *args), "ClusterRules")
    links = [r["annotations"]["runbook_url"] for g in rules["spec"]["groups"] for r in g["rules"]]
    assert links
    assert all(link.startswith(f"{url}#") for link in links)


def test_the_bootstrap_renders_the_charts_clusterrules_when_asked(
    helm: str, tmp_path: Path
) -> None:
    docs = bootstrap(helm, "dev", *_gmp_args(tmp_path))
    rules = [d for d in docs if d["kind"] == "ClusterRules"]
    assert len(rules) == 1
    assert rules[0]["apiVersion"] == "monitoring.googleapis.com/v1"
    assert "namespace" not in rules[0]["metadata"]
    names = [g["name"] for g in rules[0]["spec"]["groups"]]
    assert names == ["pod-health", "resource-usage", "service-health", "database-health"]
    exprs = [
        r["expr"] for g in rules[0]["spec"]["groups"] for r in g["rules"] if "kube_" in r["expr"]
    ]
    assert exprs
    assert all("exported_namespace=" in e for e in exprs if "kube_job_failed" not in e)
    # The namespace the selectors name is the app's, as the bootstrap derived it.
    assert 'exported_namespace="your-service-name-dev"' in exprs[0]


def test_the_deployer_role_has_no_cluster_scoped_rights(helm: str) -> None:
    for d in bootstrap(helm, "dev"):
        assert d["kind"] != "ClusterRole"
