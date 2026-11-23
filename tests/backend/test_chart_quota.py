# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The app namespace's ResourceQuota (infra/k8s/bootstrap) is sized from the chart's worst case.

The worst case is computed from the render of each environment with every optional workload on:
each Deployment at its HPA maximum (or its replica count) plus the rollout surge, plus one more
surge wave for Pods still terminating, and the migration Job. A Pod counts the way Kubernetes
counts it for quota: the sum of its containers and native sidecars, or the largest init container
when that is bigger. Raise the HPA maximum, the replicas or a request, and this test fails until
the bootstrap quota is raised with it.
"""

from __future__ import annotations

import math
import os
from typing import Any

import pytest
import yaml

from tests.backend.test_chart_pod_security import ALL_ON, PROXY_ON, render
from tests.backend.test_chart_production_guard import find_helm, require_helm
from tests.backend.test_k8s_bootstrap import BOOTSTRAP, ENVS, _one, bootstrap

HEADROOM = 1.2
APP_NAMESPACE = "svc-ns"  # the namespace render() installs into
UNITS = {
    "m": 1e-3,
    "Ki": 2**10,
    "Mi": 2**20,
    "Gi": 2**30,
    "Ti": 2**40,
    "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}
DEFAULT_SURGE = "25%"  # a Deployment with no strategy (the WAF) rolls with Kubernetes' default


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def quantity(value: object) -> float:
    text = str(value)
    for suffix, factor in sorted(UNITS.items(), key=lambda kv: -len(kv[0])):
        if text.endswith(suffix):
            return float(text.removesuffix(suffix)) * factor
    return float(text)


def _sum(containers: list[dict[str, Any]], kind: str, resource: str) -> float:
    return sum(quantity(c["resources"][kind][resource]) for c in containers if c)


def pod_cost(pod: dict[str, Any]) -> dict[str, float]:
    """One Pod's quota use: requests.cpu, requests.memory, limits.memory, pods."""
    inits = pod.get("initContainers") or []
    sidecars = [c for c in inits if c.get("restartPolicy") == "Always"]
    plain = [c for c in inits if c.get("restartPolicy") != "Always"]
    running = [*pod["containers"], *sidecars]
    cost = {"pods": 1.0}
    for kind, resource in (("requests", "cpu"), ("requests", "memory"), ("limits", "memory")):
        biggest_init = max((_sum([c], kind, resource) for c in plain), default=0.0)
        cost[f"{kind}.{resource}"] = max(biggest_init, _sum(running, kind, resource))
    return cost


def _surge(spec: dict[str, Any], peak: int) -> int:
    surge = ((spec.get("strategy") or {}).get("rollingUpdate") or {}).get("maxSurge", DEFAULT_SURGE)
    if isinstance(surge, str) and surge.endswith("%"):
        return math.ceil(peak * int(surge.removesuffix("%")) / 100)
    return int(surge)


def worst_case(docs: list[dict[str, Any]]) -> dict[str, float]:
    hpa_max = {
        d["spec"]["scaleTargetRef"]["name"]: d["spec"]["maxReplicas"]
        for d in docs
        if d["kind"] == "HorizontalPodAutoscaler"
    }
    total = dict.fromkeys(("requests.cpu", "requests.memory", "limits.memory", "pods"), 0.0)

    def add(pod: dict[str, Any], count: int) -> None:
        for key, value in pod_cost(pod).items():
            total[key] += value * count

    for d in docs:
        if d["metadata"].get("namespace") != APP_NAMESPACE:
            continue
        template: dict[str, Any] = (d.get("spec") or {}).get("template") or {}
        if d["kind"] == "Deployment":
            peak = hpa_max.get(d["metadata"]["name"], d["spec"].get("replicas", 1))
            surge = _surge(d["spec"], peak)
            add(template["spec"], peak + 2 * surge)  # the surge, and a wave still terminating
        elif d["kind"] == "Job":
            add(template["spec"], 1)
        elif d["kind"] == "CronJob":
            add(d["spec"]["jobTemplate"]["spec"]["template"]["spec"], 1)
    return total


def _worst_across_database_paths(helm: str, env: str) -> dict[str, float]:
    """Every option on, with the in-cluster database and with the Cloud SQL proxy: the larger."""
    values = f"values.{env}.yaml"
    cases = [worst_case(render(helm, values, *args)) for args in (ALL_ON, (*ALL_ON, *PROXY_ON))]
    return {key: max(case[key] for case in cases) for key in cases[0]}


@pytest.mark.parametrize("env", ENVS)
def test_the_bootstrap_quota_covers_the_charts_worst_case_with_headroom(
    helm: str, env: str
) -> None:
    worst = _worst_across_database_paths(helm, env)
    hard = _one(bootstrap(helm, env), "ResourceQuota", f"your-service-name-{env}")["spec"]["hard"]
    short = {
        key: (quantity(hard[key]), round(HEADROOM * need, 3))
        for key, need in worst.items()
        if quantity(hard[key]) < HEADROOM * need
    }
    assert short == {}, f"raise the quota in infra/k8s/bootstrap/values.yaml (have, need): {short}"


def test_the_preview_quota_covers_its_releases_at_the_preview_values_with_headroom(
    helm: str,
) -> None:
    """The preview namespace holds a release per pull request at values.preview.yaml (no option
    turned on: what a pull request turns on beyond that, the quota refuses). It is sized for
    quotaReleases of them."""
    worst = worst_case(render(helm, "values.preview.yaml"))
    boot = yaml.safe_load((BOOTSTRAP / "values.preview.yaml").read_text())
    releases = boot["quotaReleases"]
    assert releases > 1
    hard = _one(bootstrap(helm, "preview"), "ResourceQuota", "your-service-name-preview")
    hard = hard["spec"]["hard"]
    short = {
        key: (quantity(hard[key]), round(HEADROOM * releases * need, 3))
        for key, need in worst.items()
        if quantity(hard[key]) < HEADROOM * releases * need
    }
    assert short == {}, f"raise the quota in infra/k8s/bootstrap/values.preview.yaml: {short}"


def database_need(docs: list[dict[str, Any]]) -> dict[str, float]:
    """One release's use of the database namespace: its StatefulSets' Pods and volume claims, and
    any CronJob's Pod (none at values.preview.yaml: backups are off there)."""
    keys = ("requests.cpu", "requests.memory", "limits.memory", "pods")
    total = dict.fromkeys((*keys, "persistentvolumeclaims", "requests.storage"), 0.0)
    for d in docs:
        if not str(d["metadata"].get("namespace", "")).endswith("-db"):
            continue
        if d["kind"] == "StatefulSet":
            replicas = d["spec"].get("replicas", 1)
            for key, value in pod_cost(d["spec"]["template"]["spec"]).items():
                total[key] += value * replicas
            for claim in d["spec"].get("volumeClaimTemplates") or []:
                total["persistentvolumeclaims"] += replicas
                storage = claim["spec"]["resources"]["requests"]["storage"]
                total["requests.storage"] += quantity(storage) * replicas
        elif d["kind"] == "CronJob":
            pod = d["spec"]["jobTemplate"]["spec"]["template"]["spec"]
            for key, value in pod_cost(pod).items():
                total[key] += value
    return total


def test_the_preview_database_quota_holds_its_releases_and_refuses_one_more(helm: str) -> None:
    """The preview database namespace, which a pull request's chart reaches through the preview
    deployer: room for exactly quotaReleases databases at values.preview.yaml, not for one more. No
    headroom: the sum is exact and a StatefulSet replaces its Pod without a surge."""
    need = database_need(render(helm, "values.preview.yaml"))
    assert need["pods"] == 1
    assert need["persistentvolumeclaims"] == 1
    releases = yaml.safe_load((BOOTSTRAP / "values.preview.yaml").read_text())["quotaReleases"]
    docs = bootstrap(helm, "preview")
    hard = _one(docs, "ResourceQuota", "your-service-name-preview-db")["spec"]["hard"]
    assert set(hard) == set(need), "every resource a database uses is capped"
    wrong = {
        key: (hard[key], releases * value, (releases + 1) * value)
        for key, value in need.items()
        if not releases * value <= quantity(hard[key]) < (releases + 1) * value
    }
    assert wrong == {}, f"size dbQuota in infra/k8s/bootstrap/values.preview.yaml: {wrong}"
    # Its LimitRange admits the database container as the chart renders it.
    limits = _one(docs, "LimitRange", "your-service-name-preview-db")["spec"]["limits"][0]
    for d in render(helm, "values.preview.yaml"):
        if d["kind"] == "StatefulSet":
            for c in d["spec"]["template"]["spec"]["containers"]:
                memory = quantity(c["resources"]["limits"]["memory"])
                assert (
                    quantity(limits["min"]["memory"]) <= memory <= quantity(limits["max"]["memory"])
                )
                assert quantity(limits["min"]["cpu"]) <= quantity(c["resources"]["requests"]["cpu"])


def test_the_computation_counts_init_containers_and_sidecars_the_way_quota_does() -> None:
    def c(cpu: str, mem: str) -> dict[str, Any]:
        return {"resources": {"requests": {"cpu": cpu, "memory": mem}, "limits": {"memory": mem}}}

    pod = {
        "containers": [c("100m", "1Gi")],
        "initContainers": [c("50m", "2Gi"), {**c("50m", "128Mi"), "restartPolicy": "Always"}],
    }
    cost = pod_cost(pod)
    assert cost["requests.memory"] == 2 * 2**30  # the init container is bigger than 1Gi + 128Mi
    assert cost["requests.cpu"] == pytest.approx(0.15)  # the app plus the sidecar is bigger
    assert cost["pods"] == 1


def test_a_higher_hpa_maximum_is_caught(helm: str) -> None:
    """The guard bites: two more backend replicas at the maximum need more than the quota holds."""
    base = worst_case(render(helm, "values.dev.yaml", *ALL_ON))
    more = worst_case(
        render(helm, "values.dev.yaml", *ALL_ON, "--set", "hpa.backend.maxReplicas=8")
    )
    assert more["requests.memory"] > base["requests.memory"]
    assert more["pods"] >= base["pods"] + 2


def steady_requests(docs: list[dict[str, Any]]) -> dict[str, float]:
    """What the long-running Pods request at their minimum replicas (both namespaces, no Jobs)."""
    hpa_min = {
        d["spec"]["scaleTargetRef"]["name"]: d["spec"]["minReplicas"]
        for d in docs
        if d["kind"] == "HorizontalPodAutoscaler"
    }
    total = {"requests.cpu": 0.0, "requests.memory": 0.0}
    for d in docs:
        if d["kind"] not in ("Deployment", "StatefulSet"):
            continue
        count = hpa_min.get(d["metadata"]["name"], d["spec"].get("replicas", 1))
        cost = pod_cost(d["spec"]["template"]["spec"])
        for key in total:
            total[key] += cost[key] * count
    return total


@pytest.mark.parametrize(
    ("values", "args", "memory", "cpu"),
    [
        ("values.yaml", (), "5Gi", 0.85),
        ("values.yaml", ("--set", "waf.enabled=true"), "5.5Gi", 0.95),
        ("values.local.yaml", (), "3Gi", 0.55),
    ],
)
def test_the_values_note_on_the_default_requests_holds(
    helm: str, values: str, args: tuple[str, ...], memory: str, cpu: float
) -> None:
    """values.yaml's header states these sums; the quota's worst case is a separate upper bound."""
    found = steady_requests(render(helm, values, *args))
    assert found["requests.memory"] == quantity(memory)
    assert found["requests.cpu"] == pytest.approx(cpu)
