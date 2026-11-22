# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart's workloads: one source for ports and probe paths, rolling updates, resources and
graceful shutdown. Offline: the chart is rendered with helm and the objects are checked."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

import pytest
import yaml

from tests.backend.test_chart_pod_security import (
    ALL_ON,
    PROXY_ON,
    RENDERS,
    VALUES_FILES,
    pod_specs,
    render,
)
from tests.backend.test_chart_production_guard import CHART, ROOT, find_helm, require_helm
from your_service_name.backend import paths
from your_service_name.utils.versioning_api import DEFAULT_API_VERSION

BASE = f"{paths.API_PREFIX}/{DEFAULT_API_VERSION}"
BACKEND = "Deployment/svc-your-service-name-backend"
FRONTEND = "Deployment/svc-your-service-name-frontend"
NON_DEFAULT_PORTS = (
    "--set-string",
    "config.BACKEND_PORT=9100",
    "--set-string",
    "config.FRONTEND_PORT=9200",
    "--set-string",
    "config.DB_PORT=5433",
    "--set",
    "waf.service.port=9300",
    "--set",
    "waf.health.port=9310",
)
PORT_OPTIONS = ALL_ON


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def _text(helm: str, values: str, *args: str) -> str:
    docs = render(helm, values, *args)
    return str(yaml.safe_dump_all(docs))


# ---- One source for ports ------------------------------------------------------------------------


@pytest.mark.parametrize("values", VALUES_FILES)
@pytest.mark.parametrize("extra", [PORT_OPTIONS, PROXY_ON], ids=["all", "proxy"])
def test_no_default_port_literal_survives_a_port_override(
    helm: str, values: str, extra: tuple[str, ...]
) -> None:
    """With every port moved, a default literal left in the render is a hard-coded port."""
    text = _text(helm, values, *NON_DEFAULT_PORTS, *extra)
    found = sorted(set(re.findall(r"(?<![\w.-])(8000|3000|8080|8090|5432)(?![\w.-])", text)))
    assert found == []


def test_every_port_follows_the_overrides(helm: str) -> None:
    docs = render(helm, "values.dev.yaml", *NON_DEFAULT_PORTS, "--set", "waf.enabled=true")
    pods = pod_specs(docs)

    def ports(name: str) -> list[int]:
        return [p["containerPort"] for c in pods[name][0]["containers"] for p in c.get("ports", [])]

    assert ports(BACKEND) == [9100]
    assert ports(FRONTEND) == [9200]
    assert ports("Deployment/svc-your-service-name-waf") == [9300, 9310]
    assert ports("StatefulSet/svc-your-service-name-postgresql") == [5433]
    env = {e["name"]: e.get("value") for e in pods[FRONTEND][0]["containers"][0]["env"]}
    assert env["PORT"] == "9200"


# ---- One source for the probe paths --------------------------------------------------------------


def _probes(helm: str, name: str) -> dict[str, dict[str, Any]]:
    container = pod_specs(render(helm, "values.yaml"))[name][0]["containers"][0]
    return {k: container[k]["httpGet"] for k in ("startupProbe", "readinessProbe", "livenessProbe")}


def test_the_deployments_write_no_probe_path_of_their_own() -> None:
    for template in ("backend-deployment.yaml", "frontend-deployment.yaml"):
        text = (CHART / "templates" / template).read_text(encoding="utf-8")
        assert not re.search(r"^\s+path: /", text, re.MULTILINE), template


def test_the_backend_probes_use_the_helper_paths(helm: str) -> None:
    probes = _probes(helm, BACKEND)
    assert probes["readinessProbe"]["path"] == f"{BASE}{paths.HEALTH_ROUTE}"
    assert probes["startupProbe"]["path"] == f"{BASE}{paths.LIVENESS_ROUTE}"
    assert probes["livenessProbe"]["path"] == f"{BASE}{paths.LIVENESS_ROUTE}"


def test_the_frontend_probes_use_the_helper_path(helm: str) -> None:
    assert {p["path"] for p in _probes(helm, FRONTEND).values()} == {"/api/healthz"}


def test_the_probe_ports_follow_the_port_override(helm: str) -> None:
    docs = render(helm, "values.yaml", *NON_DEFAULT_PORTS)
    for name in (BACKEND, FRONTEND):
        c = pod_specs(docs)[name][0]["containers"][0]
        assert {c[k]["httpGet"]["port"] for k in ("startupProbe", "readinessProbe")} == {"http"}
        assert [p["name"] for p in c["ports"]] == ["http"]


# ---- Rolling updates ----------------------------------------------------------------------------


def _deployments(helm: str, *args: str) -> dict[str, dict[str, Any]]:
    docs = render(helm, "values.yaml", *args)
    return {d["metadata"]["name"]: d for d in docs if d["kind"] == "Deployment"}


@pytest.mark.parametrize("role", ["backend", "frontend"])
def test_a_rollout_never_drops_below_the_desired_count(helm: str, role: str) -> None:
    spec = _deployments(helm)[f"svc-your-service-name-{role}"]["spec"]
    assert spec["strategy"] == {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": 0, "maxSurge": "25%"},
    }
    assert spec["minReadySeconds"] == 10  # noqa: PLR2004
    assert spec["progressDeadlineSeconds"] == 600  # noqa: PLR2004


def test_the_rollout_values_are_configurable(helm: str) -> None:
    args = (
        "--set",
        "rollout.maxSurge=2",
        "--set",
        "rollout.minReadySeconds=20",
        "--set",
        "rollout.progressDeadlineSeconds=300",
    )
    for d in _deployments(helm, *args).values():
        if d["metadata"]["name"].endswith(("-backend", "-frontend")):
            assert d["spec"]["strategy"]["rollingUpdate"] == {"maxUnavailable": 0, "maxSurge": 2}
            assert d["spec"]["minReadySeconds"] == 20  # noqa: PLR2004
            assert d["spec"]["progressDeadlineSeconds"] == 300  # noqa: PLR2004


@pytest.mark.parametrize("role", ["backend", "frontend"])
def test_the_pdb_and_the_hpa_floor_fit_the_rollout(helm: str, role: str) -> None:
    """maxUnavailable 0 keeps every desired Pod serving, so a rollout never violates the PDB; and
    the replica floor is above minAvailable, so a node drain can still evict one Pod."""
    docs = render(helm, "values.yaml")
    pdb = next(
        d for d in docs if d["kind"] == "PodDisruptionBudget" and role in d["metadata"]["name"]
    )
    hpa = next(
        d for d in docs if d["kind"] == "HorizontalPodAutoscaler" and role in d["metadata"]["name"]
    )
    assert hpa["spec"]["minReplicas"] > pdb["spec"]["minAvailable"]
    assert "replicas" not in _deployments(helm)[f"svc-your-service-name-{role}"]["spec"]


# ---- Resources ----------------------------------------------------------------------------------


def _all_containers(pod: dict[str, Any]) -> list[dict[str, Any]]:
    return [*(pod.get("initContainers") or []), *pod["containers"]]


@pytest.mark.parametrize(
    ("values", "args"),
    RENDERS,
    ids=[f"{v}{'+all' if a == ALL_ON else '+proxy' if a else ''}" for v, a in RENDERS],
)
def test_every_container_has_a_cpu_request_a_memory_limit_equal_to_its_request_and_no_cpu_limit(
    helm: str, values: str, args: tuple[str, ...]
) -> None:
    pods = pod_specs(render(helm, values, *args))
    bad: list[str] = []
    for name, (pod, _) in pods.items():
        for c in _all_containers(pod):
            res = c.get("resources") or {}
            requests, limits = res.get("requests") or {}, res.get("limits") or {}
            if "cpu" not in requests or "cpu" in limits:
                bad.append(f"cpu: {name}/{c['name']}")
            if not limits.get("memory") or limits.get("memory") != requests.get("memory"):
                bad.append(f"memory: {name}/{c['name']}")
    assert bad == []


def test_the_containers_checked_include_every_kind_of_container(helm: str) -> None:
    names = {
        c["name"]
        for p, _ in pod_specs(render(helm, "values.dev.yaml", *ALL_ON)).values()
        for c in _all_containers(p)
    }
    assert {
        "backend",
        "frontend",
        "envoy",
        "wait-for-db",
        "migrate",
        "postgresql",
        "pg-backup",
        "pg-backup-verify",
    } <= names
    proxy = pod_specs(render(helm, "values.production.yaml", *PROXY_ON))
    assert "cloud-sql-proxy" in {c["name"] for p, _ in proxy.values() for c in _all_containers(p)}


@pytest.mark.parametrize(
    "key", ["backend", "frontend", "waitForDb", "migration", "postgresql", "pgBackup"]
)
def test_a_cpu_limit_is_set_by_a_value(helm: str, key: str) -> None:
    docs = render(helm, "values.dev.yaml", "--set-string", f"resources.{key}.limits.cpu=750m")
    limits = [
        c["resources"]["limits"]
        for p, _ in pod_specs(docs).values()
        for c in _all_containers(p)
        if c["resources"]["limits"].get("cpu") == "750m"
    ]
    assert limits
    assert all(limit["memory"] for limit in limits)


def test_the_waf_and_the_proxy_cpu_limits_are_set_by_a_value(helm: str) -> None:
    docs = render(
        helm,
        "values.production.yaml",
        *PROXY_ON,
        "--set",
        "waf.enabled=true",
        "--set-string",
        "waf.resources.limits.cpu=1",
        "--set-string",
        "cloudSqlProxy.resources.limits.cpu=250m",
    )
    found = {
        c["name"]: c["resources"]["limits"]["cpu"]
        for p, _ in pod_specs(docs).values()
        for c in _all_containers(p)
        if "cpu" in c["resources"]["limits"]
    }
    assert found == {"envoy": "1", "cloud-sql-proxy": "250m"}


PASTED = (
    "--set",
    "resources.backend.requests.cpu=300m",
    "--set",
    "resources.backend.requests.memory=1Gi",
    "--set",
    "resources.backend.limits.memory=1Gi",
    "--set-string",
    "resources.backend.limits.cpu=2",
)


def _backend_resources(helm: str, *args: str) -> dict[str, Any]:
    pod = pod_specs(render(helm, "values.dev.yaml", *args))[BACKEND][0]
    resources: dict[str, Any] = pod["containers"][0]["resources"]
    return resources


def test_a_pasted_standard_block_with_a_matching_memory_limit_renders(helm: str) -> None:
    assert _backend_resources(helm, *PASTED) == {
        "requests": {"cpu": "300m", "memory": "1Gi"},
        "limits": {"cpu": "2", "memory": "1Gi"},
    }


def test_a_memory_limit_that_differs_from_the_request_fails_the_render(helm: str) -> None:
    result = subprocess.run(
        [
            helm,
            "template",
            "svc",
            str(CHART),
            "--set",
            "resources.backend.limits.memory=2Gi",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "limits.memory" in result.stderr
    assert "requests.memory" in result.stderr


@pytest.mark.parametrize(
    ("request_memory", "limit_memory", "renders"),
    [
        ("1Gi", "1024Mi", True),
        ("1Gi", "1000Mi", False),
        ("512Mi", "536870912", True),
        ("1G", "1000M", True),
        ("1Ki", "1024", True),
        ("1Ti", "1024Gi", True),
        ("1K", "1000", True),
        ("1T", "1000G", True),
        ("1G", "1Gi", False),
    ],
)
def test_the_memory_limit_is_compared_with_the_request_as_a_quantity(
    helm: str,
    request_memory: str,
    limit_memory: str,
    renders: bool,  # noqa: FBT001
) -> None:
    result = subprocess.run(
        [
            helm,
            "template",
            "svc",
            str(CHART),
            "--set-string",
            f"resources.backend.requests.memory={request_memory}",
            "--set-string",
            f"resources.backend.limits.memory={limit_memory}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is renders, result.stderr
    if not renders:
        assert "must equal" in result.stderr


def test_a_memory_quantity_the_helper_cannot_read_is_refused(helm: str) -> None:
    result = subprocess.run(
        [
            helm,
            "template",
            "svc",
            str(CHART),
            "--set-string",
            "resources.backend.limits.memory=lots",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "lots" in result.stderr


def test_the_defaults_render_the_memory_limit_and_no_cpu_limit(helm: str) -> None:
    limits = _backend_resources(helm)["limits"]
    assert limits == {"memory": _backend_resources(helm)["requests"]["memory"]}


def test_other_limits_pass_through(helm: str) -> None:
    args = ("--set", "resources.backend.limits.ephemeral-storage=1Gi")
    assert _backend_resources(helm, *args)["limits"]["ephemeral-storage"] == "1Gi"


def test_the_schema_takes_the_standard_resource_keys(helm: str) -> None:
    args = (
        "--set",
        "resources.backend.requests.ephemeral-storage=500Mi",
        "--set",
        "resources.backend.limits.hugepages-2Mi=0",
        "--set",
        "resources.backend.limits.cpu=1",
    )
    assert str(_backend_resources(helm, *args)["limits"]["cpu"]) == "1"


def test_a_key_that_is_not_a_resource_is_refused(helm: str) -> None:
    result = subprocess.run(
        [helm, "template", "svc", str(CHART), "--set", "resources.backend.cpuLimit=1"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0


def _quantity(value: str) -> float:
    units = {"m": 1e-3, "Ki": 2**10, "Mi": 2**20, "Gi": 2**30}
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return float(value.removesuffix(suffix)) * factor
    return float(value)


@pytest.mark.parametrize("values", ["values.dev.yaml", "values.production.yaml"])
def test_every_container_fits_the_namespace_limit_range(helm: str, values: str) -> None:
    """infra/k8s/bootstrap's LimitRange refuses a container outside its min and max."""
    lr = yaml.safe_load((ROOT / "infra/k8s/bootstrap/values.yaml").read_text())["limitRange"]
    box = lr["container"]
    for args in ((), ALL_ON):
        for name, (pod, _) in pod_specs(render(helm, values, *args)).items():
            if name.startswith("StatefulSet/"):  # the database namespace has no LimitRange
                continue
            for c in _all_containers(pod):
                r = c["resources"]
                for res in ("cpu", "memory"):
                    if res in box["min"]:
                        assert _quantity(r["requests"][res]) >= _quantity(box["min"][res]), c[
                            "name"
                        ]
                    if res in box["max"]:
                        assert _quantity(r["limits"][res]) <= _quantity(box["max"][res]), c["name"]


# ---- Graceful shutdown --------------------------------------------------------------------------


def _config(docs: list[dict[str, Any]]) -> dict[str, str]:
    data: dict[str, str] = next(d for d in docs if d["kind"] == "ConfigMap")["data"]
    return data


def _grace(helm: str, name: str, *args: str) -> tuple[int, int]:
    """(terminationGracePeriodSeconds, the preStop sleep) of a Deployment."""
    docs = render(helm, "values.yaml", *args)
    pod = pod_specs(docs)[name][0]
    pre_stop = pod["containers"][0]["lifecycle"]["preStop"]
    assert set(pre_stop) == {"sleep"}, "distroless images have no shell: the native sleep action"
    return pod["terminationGracePeriodSeconds"], pre_stop["sleep"]["seconds"]


def _margin() -> int:
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    margin: int = values["shutdown"]["marginSeconds"]
    assert margin > 0
    return margin


@pytest.mark.parametrize("graceful", ["30", "45"])
def test_the_backend_grace_period_covers_prestop_graceful_timeout_and_a_margin(
    helm: str, graceful: str
) -> None:
    args = ("--set-string", f"config.BACKEND_GRACEFUL_TIMEOUT={graceful}")
    grace, sleep = _grace(helm, BACKEND, *args)
    config = _config(render(helm, "values.yaml", *args))
    assert config["BACKEND_GRACEFUL_TIMEOUT"] == graceful
    assert sleep > 0
    assert grace >= sleep + int(graceful) + _margin()


def test_the_frontend_grace_period_covers_prestop_the_drain_and_a_margin(helm: str) -> None:
    for drain in ("15", "25"):
        grace, sleep = _grace(helm, FRONTEND, "--set", f"shutdown.frontendDrainSeconds={drain}")
        assert sleep > 0
        assert grace >= sleep + int(drain) + _margin()


def test_the_prestop_sleep_and_the_margin_come_from_values(helm: str) -> None:
    args = ("--set", "shutdown.preStopSeconds=8", "--set", "shutdown.marginSeconds=12")
    assert _grace(helm, BACKEND, *args) == (8 + 30 + 12, 8)
    assert _grace(helm, FRONTEND, *args) == (8 + 15 + 12, 8)


def test_the_default_backend_graceful_timeout_is_the_launchers() -> None:
    """The chart sets the launcher's default explicitly, so the two cannot drift."""
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    serve = (ROOT / "src/your_service_name/backend/serve.py").read_text()
    default = re.search(r'"BACKEND_GRACEFUL_TIMEOUT", (\d+)', serve)
    assert default
    assert values["config"]["BACKEND_GRACEFUL_TIMEOUT"] == default.group(1)


# ---- The Kubernetes version floor ---------------------------------------------------------------


def test_the_kube_version_floor_is_the_one_the_sleep_action_and_native_sidecars_need() -> None:
    """lifecycle.preStop.sleep is beta, on by default, from 1.30; native sidecars from 1.29."""
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert chart["kubeVersion"] == ">=1.30.0-0"


def test_the_external_database_stub_follows_the_database_port(helm: str) -> None:
    docs = render(
        helm,
        "values.production.yaml",
        "--set",
        "externalDatabase.manageSecret=true",
        "--set-string",
        "config.DB_PORT=5433",
    )
    stub = next(
        d
        for d in docs
        if d["kind"] == "Secret" and d["metadata"]["name"].endswith("-db-credentials")
    )
    assert stub["stringData"]["port"] == "5433"
    refused = subprocess.run(
        [helm, "template", "svc", str(CHART), "--set", "externalDatabase.port=5433"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode != 0, (
        "externalDatabase.port is gone: config.DB_PORT is the one source"
    )
