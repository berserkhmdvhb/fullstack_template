# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart's monitoring: both flavours, one set of rule groups, and the dedicated metrics port.
Offline: the chart is rendered with helm and the objects are checked."""

from __future__ import annotations

import os
import re
from typing import Any

import pytest

from tests.backend.test_chart_pod_security import (
    ALL_ON,
    PROXY_ON,
    VALUES_FILES,
    pod_specs,
    render,
)
from tests.backend.test_chart_production_guard import CHART, find_helm, require_helm
from your_service_name.backend.main import LATENCY_BUCKETS

BACKEND = "Deployment/svc-your-service-name-backend"
FRONTEND = "Deployment/svc-your-service-name-frontend"
ON = ("--set", "monitoring.serviceMonitor=true", "--set", "monitoring.prometheusRule=true")
FLAVOURS = {
    "prometheus-operator": ("ServiceMonitor", "PrometheusRule", "monitoring", "prometheus"),
    "gmp": ("PodMonitoring", "Rules", "gmp-system", "collector"),
}


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def _by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d["kind"] == kind]


def _flavour(helm: str, values: str, flavour: str, *args: str) -> list[dict[str, Any]]:
    return render(helm, values, "--set", f"monitoring.flavour={flavour}", *ON, *args)


CLUSTER_ON = ("--set", "monitoring.gmp.clusterRules.enabled=true")
APP_GROUPS = ["app-health", "slo-recording", "slo-alerts"]


def _kube_exprs(groups: list[dict[str, Any]]) -> list[str]:
    return [r["expr"] for g in groups for r in g["rules"] if "kube_" in r["expr"]]


@pytest.mark.parametrize("values", VALUES_FILES)
def test_the_rule_groups_are_one_source_split_across_the_gmp_objects(
    helm: str, values: str
) -> None:
    """Both flavours take the same groups: gmp splits them into app (Rules) and cluster."""
    operator = _by_kind(_flavour(helm, values, "prometheus-operator"), "PrometheusRule")
    gmp_docs = _flavour(helm, values, "gmp", *CLUSTER_ON)
    rules = _by_kind(gmp_docs, "Rules")
    cluster = _by_kind(gmp_docs, "ClusterRules")
    assert len(operator) == len(rules) == len(cluster) == 1
    groups = operator[0]["spec"]["groups"]
    assert groups
    app_groups, cluster_groups = rules[0]["spec"]["groups"], cluster[0]["spec"]["groups"]
    assert not _kube_exprs(app_groups), "the namespaced Rules must hold no kube_* rule"
    assert [g["name"] for g in app_groups] == APP_GROUPS
    # The operator's groups are the same two sets (its selectors say `namespace`, gmp's the label).
    assert [g["name"] for g in groups] == [g["name"] for g in [*cluster_groups, *app_groups]]
    assert app_groups == groups[-len(APP_GROUPS) :]


def test_the_charts_latency_buckets_are_the_backends() -> None:
    """chart.latencyBuckets is the backend's LATENCY_BUCKETS, as the client renders `le`."""
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    found = re.search(r'define "chart.latencyBuckets" -}}([^{]*){{-', helpers)
    assert found, "_helpers.tpl no longer defines chart.latencyBuckets"
    # str(1.0) is "1.0", as the client writes `le` (test_metrics_server.py reads the served bounds).
    assert found.group(1).split() == [str(b) for b in LATENCY_BUCKETS]


@pytest.mark.parametrize("label", ["exported_namespace", "kube_namespace"])
def test_every_kube_selector_of_the_gmp_cluster_rules_uses_the_object_namespace_label(
    helm: str, label: str
) -> None:
    docs = _flavour(
        helm,
        "values.yaml",
        "gmp",
        *CLUSTER_ON,
        "--set",
        f"monitoring.gmp.objectNamespaceLabel={label}",
    )
    exprs = _kube_exprs(_by_kind(docs, "ClusterRules")[0]["spec"]["groups"])
    assert exprs
    for expr in exprs:
        for selector in re.findall(r"kube_\w+\{([^}]*)\}", expr):
            assert f'{label}="' in selector, expr
            assert "namespace=" not in selector.replace(f"{label}=", ""), expr


def test_the_operator_flavour_keeps_the_namespace_label(helm: str) -> None:
    docs = _flavour(helm, "values.yaml", "prometheus-operator")
    for expr in _kube_exprs(_by_kind(docs, "PrometheusRule")[0]["spec"]["groups"]):
        assert "exported_namespace" not in expr
        if "kube_job_failed" not in expr:  # selected by job name alone, as before
            assert 'namespace="' in expr


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_the_deploy_never_renders_cluster_scoped_rules(helm: str, flavour: str) -> None:
    """The deployer has no cluster rights: ClusterRules only come from the admin bootstrap."""
    for values in VALUES_FILES:
        docs = render(helm, values, "--set", f"monitoring.flavour={flavour}", *ALL_ON)
        assert not _by_kind(docs, "ClusterRules"), values


def test_every_ingress_rule_that_names_an_extra_peer_admits_the_metrics_port_only(
    helm: str,
) -> None:
    """Whatever the layout (WAF, extra API peers, gmp): the extra scrape peers reach one port."""
    extra = "10.20.0.0/24"
    for waf in ("false", "true"):
        docs = render(
            helm,
            "values.production.yaml",
            "--set", f"waf.enabled={waf}",
            "--set", "monitoring.flavour=gmp",
            "--set", f"networkPolicy.monitoringExtraPeers[0].ipBlock.cidr={extra}",
            "--set", "networkPolicy.backendExtraIngressPeers[0].ipBlock.cidr=10.30.0.0/24",
        )  # fmt: skip
        found = 0
        for policy in _by_kind(docs, "NetworkPolicy"):
            for rule in policy["spec"].get("ingress") or []:
                if {"ipBlock": {"cidr": extra}} in rule["from"]:
                    found += 1
                    assert rule["ports"] == [{"protocol": "TCP", "port": 9102}], policy
        assert found == 1


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_each_flavour_renders_only_its_own_objects(helm: str, flavour: str) -> None:
    docs = _flavour(helm, "values.yaml", flavour)
    monitor, rules, _, _ = FLAVOURS[flavour]
    other = {k for f, v in FLAVOURS.items() if f != flavour for k in v[:2]}
    kinds = {d["kind"] for d in docs}
    assert {monitor, rules} <= kinds
    assert not other & kinds


@pytest.mark.parametrize("flavour", FLAVOURS)
def test_the_monitor_targets_a_named_port_the_pod_and_the_service_both_have(
    helm: str, flavour: str
) -> None:
    docs = _flavour(helm, "values.yaml", flavour, "--set-string", "config.METRICS_PORT=9105")
    monitor = _by_kind(docs, FLAVOURS[flavour][0])[0]
    endpoints = monitor["spec"]["endpoints"]
    assert [e["port"] for e in endpoints] == ["metrics"]
    pod_ports = {
        p["name"]: p["containerPort"] for p in pod_specs(docs)[BACKEND][0]["containers"][0]["ports"]
    }
    service = next(
        d for d in _by_kind(docs, "Service") if d["metadata"]["name"].endswith("-backend")
    )
    service_ports = {p["name"]: p["port"] for p in service["spec"]["ports"]}
    assert pod_ports["metrics"] == service_ports["metrics"] == 9105  # noqa: PLR2004
    assert pod_ports["http"] != pod_ports["metrics"]
    assert service["metadata"]["annotations"]["prometheus.io/port"] == "9105"


def test_the_frontend_has_no_metrics_port(helm: str) -> None:
    """The frontend exposes no metrics today: its only port is its own."""
    docs = render(helm, "values.yaml", *ON)
    ports = pod_specs(docs)[FRONTEND][0]["containers"][0]["ports"]
    assert [p["name"] for p in ports] == ["http"]


@pytest.mark.parametrize("values", VALUES_FILES)
def test_every_container_port_in_each_pod_is_unique_with_every_option_on(
    helm: str, values: str
) -> None:
    """The metrics port clashes with nothing, with every option and the Cloud SQL proxy on."""
    for flavour in FLAVOURS:
        args = ("--set", f"monitoring.flavour={flavour}", *ALL_ON, *PROXY_ON)
        for name, (pod, _) in pod_specs(render(helm, values, *args)).items():
            ports = [
                p["containerPort"]
                for c in [*pod["containers"], *(pod.get("initContainers") or [])]
                for p in c.get("ports", [])
            ]
            assert len(ports) == len(set(ports)), (name, ports)


@pytest.mark.parametrize("flavour", FLAVOURS)
@pytest.mark.parametrize("values", VALUES_FILES)
def test_the_scrape_allow_covers_the_metrics_port_only(
    helm: str, values: str, flavour: str
) -> None:
    docs = render(
        helm,
        values,
        "--set",
        f"monitoring.flavour={flavour}",
        "--set-string",
        "config.METRICS_PORT=9105",
    )
    _, _, namespace, pod = FLAVOURS[flavour]
    peer = {
        "namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": namespace}},
        "podSelector": {"matchLabels": {"app.kubernetes.io/name": pod}},
    }
    rules = [
        (policy["metadata"]["name"], rule)
        for policy in _by_kind(docs, "NetworkPolicy")
        for rule in policy["spec"].get("ingress") or []
        if peer in rule["from"]
    ]
    assert [n for n, _ in rules] == ["svc-your-service-name-allow-to-backend"]
    assert rules[0][1]["ports"] == [{"protocol": "TCP", "port": 9105}]
    # And no other rule of any policy admits anything to the metrics port.
    for policy in _by_kind(docs, "NetworkPolicy"):
        for rule in policy["spec"].get("ingress") or []:
            if {"protocol": "TCP", "port": 9105} in rule["ports"]:
                assert peer in rule["from"], policy["metadata"]["name"]
