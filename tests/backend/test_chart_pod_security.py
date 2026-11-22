# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Every Pod template the chart renders, in every mode and option, meets the restricted Pod Security
Standard (https://kubernetes.io/docs/concepts/security/pod-security-standards/, restricted profile).

Offline: the chart is rendered with helm and each Pod spec is checked against the profile's
documented rules. The namespaces enforce restricted (infra/k8s/bootstrap), so a Pod failing here is
refused there.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from typing import Any

import pytest
import yaml

from tests.backend.test_chart_production_guard import CHART, find_helm, require_helm

# The values files a release is rendered from (CI lints and renders each one).
VALUES_FILES = [
    "values.yaml",
    *sorted(p.name for p in CHART.glob("values.*.yaml")),
    *sorted(f"ci/{p.name}" for p in (CHART / "ci").glob("*-values.yaml")),
]
# Every optional workload or kind on at once. Layered on each values file, so each edge mode and
# both database paths get every option.
ALL_ON = (
    "--set",
    "waf.enabled=true",
    "--set",
    "edge.gateway.gke.enabled=true",
    "--set",
    "monitoring.serviceMonitor=true",
    "--set",
    "monitoring.prometheusRule=true",
    "--set",
    "externalDatabase.manageSecret=true",
    "--set",
    "observability.tracing.enabled=true",
    "--set",
    "observability.tracing.endpoint=http://collector.monitoring.svc:4318",
    "--set",
    "cspReporting.enabled=true",
)
RENDERS = [(f, ()) for f in VALUES_FILES] + [(f, ALL_ON) for f in VALUES_FILES]

ALLOWED_VOLUMES = {
    "configMap",
    "csi",
    "downwardAPI",
    "emptyDir",
    "ephemeral",
    "persistentVolumeClaim",
    "projected",
    "secret",
}
ALLOWED_SELINUX_TYPES = {
    "",
    "container_t",
    "container_init_t",
    "container_kvm_t",
    "container_engine_t",
}
# The restricted rule list, one line each; the checker below reports these names.
RULES = (
    "host namespaces",
    "privileged",
    "hostPath and other volume types",
    "hostPort",
    "runAsNonRoot",
    "runAsUser 0",
    "allowPrivilegeEscalation",
    "capabilities",
    "seccompProfile",
    "procMount",
    "sysctls",
    "AppArmor",
    "SELinux",
    "Windows hostProcess",
)


def render(helm: str, values: str, *args: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [helm, "template", "svc", str(CHART), "-n", "svc-ns", "-f", str(CHART / values), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def pod_specs(docs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every Pod spec, keyed by kind/name."""
    out: dict[str, dict[str, Any]] = {}
    for d in docs:
        kind, spec = d["kind"], d.get("spec") or {}
        if kind == "Pod":
            pod = spec
        elif kind == "CronJob":
            pod = spec["jobTemplate"]["spec"]["template"]["spec"]
        elif kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}:
            pod = spec["template"]["spec"]
        else:
            continue
        out[f"{kind}/{d['metadata']['name']}"] = pod
    return out


def _context_violations(ctx: dict[str, Any], name: str) -> list[str]:
    """The rules a Pod or container securityContext alone can break."""
    bad = []
    if (ctx.get("appArmorProfile") or {}).get("type") == "Unconfined":
        bad.append(f"AppArmor: {name}")
    se = ctx.get("seLinuxOptions") or {}
    if se.get("type", "") not in ALLOWED_SELINUX_TYPES or se.get("user") or se.get("role"):
        bad.append(f"SELinux: {name}")
    if (ctx.get("windowsOptions") or {}).get("hostProcess"):
        bad.append(f"Windows hostProcess: {name}")
    return bad


def _container_violations(c: dict[str, Any], psc: dict[str, Any]) -> list[str]:
    """The rules one container breaks; psc is the Pod securityContext it inherits from."""
    sc = c.get("securityContext") or {}
    caps = sc.get("capabilities") or {}
    seccomp = (sc.get("seccompProfile") or psc.get("seccompProfile") or {}).get("type")
    checks = {
        "privileged": bool(sc.get("privileged")),
        "hostPort": any(p.get("hostPort") for p in c.get("ports") or []),
        "runAsNonRoot": sc.get("runAsNonRoot", psc.get("runAsNonRoot")) is not True,
        "runAsUser 0": 0 in (sc.get("runAsUser"), psc.get("runAsUser")),
        "allowPrivilegeEscalation": sc.get("allowPrivilegeEscalation") is not False,
        "capabilities": "ALL" not in (caps.get("drop") or [])
        or bool(set(caps.get("add") or []) - {"NET_BIND_SERVICE"}),
        "seccompProfile": seccomp not in {"RuntimeDefault", "Localhost"},
        "procMount": sc.get("procMount", "Default") != "Default",
    }
    bad = [f"{rule}: {c['name']}" for rule, broken in checks.items() if broken]
    return bad + _context_violations(psc, c["name"]) + _context_violations(sc, c["name"])


def violations(pod: dict[str, Any]) -> list[str]:
    """The restricted rules this Pod spec breaks (empty when it complies)."""
    bad: list[str] = []
    psc = pod.get("securityContext") or {}
    if any(pod.get(f) for f in ("hostNetwork", "hostPID", "hostIPC")):
        bad.append("host namespaces")
    bad += [
        f"hostPath and other volume types: {vol['name']}"
        for vol in pod.get("volumes") or []
        if not set(vol) - {"name"} <= ALLOWED_VOLUMES
    ]
    if psc.get("sysctls"):
        bad.append("sysctls")
    for key in ("initContainers", "containers", "ephemeralContainers"):
        for c in pod.get(key) or []:
            bad += _container_violations(c, psc)
    return bad


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


@pytest.mark.parametrize(
    ("values", "args"), RENDERS, ids=[f"{v}{'+all' if a else ''}" for v, a in RENDERS]
)
def test_every_pod_template_meets_the_restricted_profile(
    helm: str, values: str, args: tuple[str, ...]
) -> None:
    pods = pod_specs(render(helm, values, *args))
    assert pods
    found = {name: violations(pod) for name, pod in pods.items()}
    assert {name: bad for name, bad in found.items() if bad} == {}


def test_the_in_cluster_postgresql_and_its_jobs_are_checked(helm: str) -> None:
    pods = pod_specs(render(helm, "values.dev.yaml", *ALL_ON))
    assert "StatefulSet/svc-your-service-name-postgresql" in pods
    assert {
        "CronJob/svc-your-service-name-pg-backup",
        "CronJob/svc-your-service-name-pg-backup-verify",
    } <= set(pods)
    assert "Deployment/svc-your-service-name-waf" in pods
    assert any(n.startswith("Job/svc-your-service-name-migrate") for n in pods)


def _compliant() -> dict[str, Any]:
    return {
        "securityContext": {"runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
        "volumes": [{"name": "tmp", "emptyDir": {}}],
        "initContainers": [],
        "containers": [
            {
                "name": "c",
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "capabilities": {"drop": ["ALL"]},
                },
            }
        ],
    }


BREAKS: dict[str, Callable[[dict[str, Any]], object]] = {
    "host namespaces": lambda p: p.update(hostNetwork=True),
    "hostPath and other volume types": lambda p: p["volumes"].append(
        {"name": "h", "hostPath": {"path": "/"}}
    ),
    "sysctls": lambda p: p["securityContext"].update(sysctls=[{"name": "kernel.x", "value": "1"}]),
    "privileged": lambda p: p["containers"][0]["securityContext"].update(privileged=True),
    "hostPort": lambda p: p["containers"][0].update(ports=[{"containerPort": 80, "hostPort": 80}]),
    "runAsNonRoot": lambda p: p["securityContext"].pop("runAsNonRoot"),
    "runAsUser 0": lambda p: p["containers"][0]["securityContext"].update(runAsUser=0),
    "allowPrivilegeEscalation": lambda p: p["containers"][0]["securityContext"].pop(
        "allowPrivilegeEscalation"
    ),
    "capabilities": lambda p: p["containers"][0]["securityContext"].update(
        capabilities={"drop": ["ALL"], "add": ["NET_ADMIN"]}
    ),
    "seccompProfile": lambda p: p["containers"][0]["securityContext"].update(
        seccompProfile={"type": "Unconfined"}
    ),
    "procMount": lambda p: p["containers"][0]["securityContext"].update(procMount="Unmasked"),
    "AppArmor": lambda p: p["securityContext"].update(appArmorProfile={"type": "Unconfined"}),
    "SELinux": lambda p: p["securityContext"].update(seLinuxOptions={"type": "spc_t"}),
    "Windows hostProcess": lambda p: p["securityContext"].update(
        windowsOptions={"hostProcess": True}
    ),
}


def test_the_checker_passes_a_compliant_pod_and_flags_each_rule() -> None:
    assert violations(_compliant()) == []
    assert set(BREAKS) == set(RULES)
    for rule, breaks in BREAKS.items():
        pod = _compliant()
        breaks(pod)
        assert [b for b in violations(pod) if b.startswith(rule)], rule
    # An init container is held to the same rules.
    pod = _compliant()
    pod["initContainers"].append({"name": "i", "securityContext": {}})
    assert violations(pod)
