# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart's monitoring: both flavours, one set of rule groups, and the dedicated metrics port.
Offline: the chart is rendered with helm and the objects are checked."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.backend.test_chart_pod_security import (
    ALL_ON,
    PROXY_ON,
    VALUES_FILES,
    pod_specs,
    render,
)
from tests.backend.test_chart_production_guard import find_helm, require_helm

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


@pytest.mark.parametrize("values", VALUES_FILES)
def test_the_rule_groups_are_identical_across_the_flavours(helm: str, values: str) -> None:
    operator = _by_kind(_flavour(helm, values, "prometheus-operator"), "PrometheusRule")
    gmp = _by_kind(_flavour(helm, values, "gmp"), "Rules")
    assert len(operator) == len(gmp) == 1
    groups = operator[0]["spec"]["groups"]
    assert groups
    assert groups == gmp[0]["spec"]["groups"]


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
