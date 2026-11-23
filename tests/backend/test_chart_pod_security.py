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
    "--set",
    "waf.owaspCrs=true",
    "--set",
    "hpa.backend.enabled=true",
    "--set",
    "hpa.frontend.enabled=true",
    "--set",
    "postgresql.backup.enabled=true",
    "--set",
    "monitoring.scrapeAnnotations=true",
    "--set",
    "monitoring.alerts.certificateExpiry.enabled=true",
    "--set",
    "monitoring.dashboards.enabled=true",
    # The secret sources are exclusive: the ExternalSecret replaces production's existingSecret, and
    # the in-cluster database (dev, qa, local) then takes its password from its own Secret.
    "--set",
    "secrets.externalSecret.enabled=true",
    "--set",
    "secrets.existingSecret=",
    "--set",
    "postgresql.existingSecret=pg-credentials",
)
# The Cloud SQL Auth Proxy needs a managed database and a loopback host with TLS off, so it is
# its own set: layered on every values file, with the in-cluster database off.
PROXY_ON = (
    "--set",
    "postgresql.enabled=false",
    "--set",
    "config.DB_SSLMODE=disable",
    "--set",
    "cloudSqlProxy.enabled=true",
    "--set",
    "cloudSqlProxy.instanceConnectionName=proj:region:inst",
    "--set",
    "cloudSqlProxy.egressCIDRs[0]=199.36.153.8/30",
    "--set",
    "cloudSqlProxy.privateIp=true",
    "--set",
    "cloudSqlProxy.autoIamAuthn=true",
)
# The helm test hooks: every option on plus tests.enabled, on every values file but production's,
# whose render refuses tests.enabled.
TESTS_ON = (*ALL_ON, "--set", "tests.enabled=true")
RENDERS = (
    [(f, ()) for f in VALUES_FILES]
    + [(f, ALL_ON) for f in VALUES_FILES]
    + [(f, PROXY_ON) for f in VALUES_FILES]
    + [(f, TESTS_ON) for f in VALUES_FILES if f != "values.production.yaml"]
)
RENDER_IDS = [
    f"{v}{ {ALL_ON: '+all', PROXY_ON: '+proxy', TESTS_ON: '+tests'}.get(a, '') }"
    for v, a in RENDERS
]

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
# The seccomp and AppArmor profile types the restricted profile allows.
ALLOWED_PROFILES = {"RuntimeDefault", "Localhost"}
APPARMOR_ANNOTATION = "container.apparmor.security.beta.kubernetes.io/"
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


def pod_specs(docs: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], dict[str, str]]]:
    """Every Pod spec with its Pod annotations, keyed by kind/name."""
    out: dict[str, tuple[dict[str, Any], dict[str, str]]] = {}
    for d in docs:
        kind, spec = d["kind"], d.get("spec") or {}
        if kind == "Pod":
            template = d
        elif kind == "CronJob":
            template = spec["jobTemplate"]["spec"]["template"]
        elif kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}:
            template = spec["template"]
        else:
            continue
        annotations = (template.get("metadata") or {}).get("annotations") or {}
        out[f"{kind}/{d['metadata']['name']}"] = (template["spec"], annotations)
    return out


def _context_violations(ctx: dict[str, Any], name: str) -> list[str]:
    """The rules a Pod or container securityContext alone can break."""
    bad = []
    if (ctx.get("appArmorProfile") or {}).get("type", "RuntimeDefault") not in ALLOWED_PROFILES:
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
        "seccompProfile": seccomp not in ALLOWED_PROFILES,
        "procMount": sc.get("procMount", "Default") != "Default",
    }
    bad = [f"{rule}: {c['name']}" for rule, broken in checks.items() if broken]
    return bad + _context_violations(psc, c["name"]) + _context_violations(sc, c["name"])


def violations(pod: dict[str, Any], annotations: dict[str, str] | None = None) -> list[str]:
    """The restricted rules a Pod spec and its Pod annotations break; empty when it complies."""
    bad: list[str] = []
    psc = pod.get("securityContext") or {}
    if any(pod.get(f) for f in ("hostNetwork", "hostPID", "hostIPC")):
        bad.append("host namespaces")
    # A Pod-level profile must be allowed too, even where every container overrides it.
    if (psc.get("seccompProfile") or {}).get("type", "RuntimeDefault") not in ALLOWED_PROFILES:
        bad.append("seccompProfile: pod")
    bad += [
        f"AppArmor: {key.removeprefix(APPARMOR_ANNOTATION)}"
        for key, value in (annotations or {}).items()
        if key.startswith(APPARMOR_ANNOTATION)
        and value != "runtime/default"
        and not value.startswith("localhost/")
    ]
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
    ("values", "args"),
    RENDERS,
    ids=RENDER_IDS,
)
def test_every_pod_template_meets_the_restricted_profile(
    helm: str, values: str, args: tuple[str, ...]
) -> None:
    pods = pod_specs(render(helm, values, *args))
    assert pods
    found = {name: violations(spec, annotations) for name, (spec, annotations) in pods.items()}
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


def test_a_pod_level_unconfined_seccomp_fails_even_when_every_container_sets_runtime_default() -> (
    None
):
    pod = _compliant()
    pod["securityContext"]["seccompProfile"] = {"type": "Unconfined"}
    pod["containers"][0]["securityContext"]["seccompProfile"] = {"type": "RuntimeDefault"}
    assert [b for b in violations(pod) if b.startswith("seccompProfile")]


@pytest.mark.parametrize("profile", ["Unconfined", "unknown"])
def test_an_apparmor_field_other_than_runtime_default_or_localhost_fails(profile: str) -> None:
    for where in ("pod", "container"):
        pod = _compliant()
        ctx = pod["securityContext"] if where == "pod" else pod["containers"][0]["securityContext"]
        ctx["appArmorProfile"] = {"type": profile}
        assert [b for b in violations(pod) if b.startswith("AppArmor")], where
    for ok in ("RuntimeDefault", "Localhost"):
        pod = _compliant()
        pod["securityContext"]["appArmorProfile"] = {"type": ok}
        assert violations(pod) == []


@pytest.mark.parametrize(
    ("value", "bad"),
    [("unconfined", True), ("other", True), ("runtime/default", False), ("localhost/p", False)],
)
def test_the_apparmor_annotation_form_is_checked_too(value: str, bad: bool) -> None:  # noqa: FBT001
    annotations = {"container.apparmor.security.beta.kubernetes.io/c": value}
    found = [b for b in violations(_compliant(), annotations) if b.startswith("AppArmor")]
    assert bool(found) is bad


# Booleans in values.yaml that ALL_ON leaves as they are, each with its reason.
NOT_ALL_ON = {
    "guards.rejectPlaceholders": "on, it refuses the placeholder hosts every CI render uses",
    "namespaces.create": "admin-only objects (the single-tenant path); never the deployer's",
    "rbac.create": "admin-only objects: the bootstrap renders the app's Role and RoleBinding",
    "serviceAccount.automountServiceAccountToken": "a Pod field, not a kind; true only weakens",
    "postgresql.enabled": "a database path, not an option: the values files render both",
    "externalDatabase.enabled": "a database path, not an option: the values files render both",
    "monitoring.gmp.clusterRules.enabled": "cluster-scoped: the bootstrap renders it",
    "monitoring.slo.enabled": "on by default: a render with the rules on carries the SLO groups",
    "cloudSqlProxy.enabled": "needs a managed database, so it fails with the in-cluster: PROXY_ON",
    "cloudSqlProxy.privateIp": "a proxy flag: PROXY_ON turns it on with the proxy",
    "cloudSqlProxy.autoIamAuthn": "a proxy flag: PROXY_ON turns it on with the proxy",
    "tests.enabled": "production refuses it: TESTS_ON turns it on for every other values file",
    "tests.migration.fail": "test-only: the migration Job fails on purpose (no Pod spec changes)",
}


def _toggles(node: object, prefix: str = "") -> set[str]:
    if isinstance(node, bool):
        return {prefix}
    if isinstance(node, dict):
        return {t for k, v in node.items() for t in _toggles(v, f"{prefix}{k}.")}
    return set()


def toggles(values: dict[str, Any]) -> set[str]:
    return {t.rstrip(".") for t in _toggles(values)}


def all_on_true(args: tuple[str, ...]) -> set[str]:
    return {a.removesuffix("=true") for a in args if a.endswith("=true")}


def test_all_on_turns_on_every_boolean_toggle_but_the_listed_ones() -> None:
    found = toggles(yaml.safe_load((CHART / "values.yaml").read_text()))
    assert set(NOT_ALL_ON) <= found, "a stale exclusion"
    assert found - set(NOT_ALL_ON) == all_on_true(ALL_ON)


def test_a_new_toggle_left_out_of_all_on_is_caught() -> None:
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    values["podMonitor"] = {"enabled": False}
    assert toggles(values) - set(NOT_ALL_ON) != all_on_true(ALL_ON)
