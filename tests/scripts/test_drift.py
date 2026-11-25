"""scripts/drift.py: the render against the live objects, over fixture objects (no cluster)."""

from __future__ import annotations

import copy
import importlib
import json
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
REL = "svc"
APP, DB = "svc-dev", "svc-dev-db"
LABELS = {"app.kubernetes.io/instance": REL, "app.kubernetes.io/name": "svc"}
BACKEND = "spec.template.spec.containers[backend]"
SENTINEL = "SENTINEL-VALUE-NEVER-PRINTED"


def _mod() -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    try:
        return importlib.import_module("drift")
    finally:
        sys.path.remove(str(SCRIPTS))


drift = _mod()
Obj = dict[str, Any]


def _obj(kind: str, name: str, ns: str = APP, api: str = "v1", **rest: Any) -> Obj:  # noqa: ANN401
    meta: Obj = {"name": name, "namespace": ns, "labels": dict(LABELS)}
    meta.update(rest.pop("meta", {}))
    return {"apiVersion": api, "kind": kind, "metadata": meta, **rest}


def _deployment(name: str, **spec: Any) -> Obj:  # noqa: ANN401
    container = {
        "name": name.removeprefix("svc-"),
        "image": f"reg.example/{name}@sha256:" + "a" * 64,
        "env": [{"name": "LOG_LEVEL", "value": "INFO"}, {"name": "EMPTY", "value": ""}],
        "ports": [{"name": "http", "containerPort": 8000}],
        "resources": {"requests": {"cpu": "0.5", "memory": "1Gi"}, "limits": {"memory": "1Gi"}},
        "securityContext": {
            "readOnlyRootFilesystem": True,
            "privileged": False,
            "allowPrivilegeEscalation": False,
            "runAsNonRoot": True,
        },
        "volumeMounts": [{"name": "tmp", "mountPath": "/work", "readOnly": False}],
    }
    pod = {"containers": [container], "automountServiceAccountToken": False}
    pod["enableServiceLinks"] = False
    template = {"metadata": {"labels": dict(LABELS)}, "spec": pod}
    return _obj("Deployment", name, api="apps/v1", spec={**spec, "template": template})


def rendered() -> list[Obj]:
    hook = {"annotations": {"helm.sh/hook": "test"}}
    return [
        _deployment("svc-backend", replicas=2),
        _deployment("svc-frontend"),
        _obj(
            "HorizontalPodAutoscaler",
            "svc-frontend",
            api="autoscaling/v2",
            spec={
                "scaleTargetRef": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "name": "svc-frontend",
                },
                "minReplicas": 2,
                "maxReplicas": 4,
            },
        ),
        _obj("ConfigMap", "svc-config", data={"A": "1", "B": "x"}),
        _obj("PodDisruptionBudget", "svc-backend", api="policy/v1", spec={"minAvailable": 1}),
        _obj(
            "ExternalSecret",
            "svc-secret",
            api="external-secrets.io/v1",
            spec={"target": {"name": "svc-secret"}, "refreshInterval": "1h"},
        ),
        _obj("Secret", "svc-db-credentials", ns=DB, stringData={"password": SENTINEL}),
        _obj("Job", "svc-migrate-r1", api="batch/v1", spec={"backoffLimit": 1}),
        _obj("NetworkPolicy", "svc-test-egress", api="networking.k8s.io/v1", meta=hook, spec={}),
        _obj(
            "StatefulSet",
            "svc-postgresql",
            ns=DB,
            api="apps/v1",
            spec={
                "replicas": 1,
                "volumeClaimTemplates": [
                    {
                        "metadata": {"name": "postgresql-data"},
                        "spec": {"resources": {"requests": {"storage": "1Gi"}}},
                    }
                ],
            },
        ),
    ]


def _server(obj: Obj) -> Obj:
    """What the API server and the controllers add to an applied object."""
    live = copy.deepcopy(obj)
    live.pop("stringData", None)
    meta = live["metadata"]
    meta.update(
        uid="u-1", resourceVersion="42", generation=3, creationTimestamp="2026-11-01T00:00:00Z"
    )
    meta["managedFields"] = [{"manager": "helm", "operation": "Apply"}]
    meta.setdefault("annotations", {})["meta.helm.sh/release-name"] = REL
    meta["labels"]["controller-added"] = "yes"
    live["status"] = {"observedGeneration": 3, "replicas": 2}
    spec = live.get("spec", {})
    if obj["kind"] == "Deployment":
        meta["annotations"]["deployment.kubernetes.io/revision"] = "7"
        spec.setdefault("replicas", 3)  # the HPA's count
        spec["strategy"] = {"type": "RollingUpdate"}
        spec["revisionHistoryLimit"] = 10
        pod = spec["template"]["spec"]
        pod.update(
            restartPolicy="Always", dnsPolicy="ClusterFirst", schedulerName="default-scheduler"
        )
        for c in pod["containers"]:
            c.update(terminationMessagePath="/dev/termination-log", imagePullPolicy="IfNotPresent")
            c["env"] = [e if e["value"] else {"name": e["name"]} for e in c["env"]]  # "" is omitted
            c["ports"][0]["protocol"] = "TCP"
            c["resources"]["requests"]["cpu"] = "500m"
            c["volumeMounts"][0].pop("readOnly")  # a plain bool: false is omitted
    if obj["kind"] == "ExternalSecret":
        live["status"] = {
            "conditions": [{"type": "Ready", "status": "True", "reason": "SecretSynced"}]
        }
    if obj["kind"] == "StatefulSet":
        for t in spec["volumeClaimTemplates"]:
            t.update(apiVersion="v1", kind="PersistentVolumeClaim", status={"phase": "Pending"})
    return live


def live_objects() -> list[Obj]:
    objs = [
        _server(o) for o in rendered() if o["kind"] != "Secret" and "helm.sh/hook" not in str(o)
    ]
    objs = [o for o in objs if o["kind"] != "Job"]
    # The migration Jobs of the live revisions, a CronJob's Job and a StatefulSet's claim.
    objs += [_server(_obj("Job", f"svc-migrate-r{n}", api="batch/v1", spec={})) for n in (6, 7)]
    owned = {"ownerReferences": [{"kind": "CronJob", "name": "svc-pg-backup"}]}
    objs.append(_server(_obj("Job", "svc-pg-backup-29000000", api="batch/v1", meta=owned)))
    objs.append(_server(_obj("PersistentVolumeClaim", "postgresql-data-svc-postgresql-0", ns=DB)))
    # A hook that ran and a ConfigMap of another release, both ignored.
    objs.append(_server(_obj("NetworkPolicy", "svc-test-egress", api="networking.k8s.io/v1")))
    objs[-1]["metadata"]["annotations"]["helm.sh/hook"] = "test"
    other = _server(_obj("ConfigMap", "other-config"))
    other["metadata"]["labels"]["app.kubernetes.io/instance"] = "other"
    return [*objs, other]


class FakeLive:
    """The cluster as a list of objects; records each read."""

    def __init__(self, objects: list[Obj]) -> None:
        self.objects = objects
        self.reads: list[tuple[str, str, str]] = []

    def _match(self, obj: Obj, kind: str, ns: str) -> bool:
        return bool(obj["kind"] == kind and obj["metadata"]["namespace"] == ns)

    def list(self, _api_version: str, kind: str, namespace: str, selector: str) -> list[Obj]:
        self.reads.append(("list", kind, namespace))
        key, _, value = selector.partition("=")
        return [
            copy.deepcopy(o)
            for o in self.objects
            if self._match(o, kind, namespace) and o["metadata"].get("labels", {}).get(key) == value
        ]

    def get(self, _api_version: str, kind: str, namespace: str, name: str) -> Obj | None:
        self.reads.append(("get", kind, namespace))
        hits = [
            o
            for o in self.objects
            if self._match(o, kind, namespace) and o["metadata"]["name"] == name
        ]
        return copy.deepcopy(hits[0]) if hits else None

    def find(self, kind: str, name: str) -> Obj:
        return next(o for o in self.objects if o["kind"] == kind and o["metadata"]["name"] == name)


def _run(live: FakeLive) -> tuple[set[tuple[str, str, str, str]], list[dict[str, str]]]:
    findings, notes = drift.check(rendered(), REL, live)
    return {(f["kind"], f["name"], f["path"], f["change"]) for f in findings}, notes


@pytest.fixture
def live() -> FakeLive:
    return FakeLive(live_objects())


def test_a_clean_render_with_server_defaults_reports_nothing(live: FakeLive) -> None:
    found, notes = _run(live)
    assert found == set()
    reasons = {(n["kind"], n["name"]): n["reason"] for n in notes}
    assert "Secrets" in reasons[("Secret", "svc-db-credentials")]
    assert "Helm revision" in reasons[("Job", "svc-migrate-r1")]
    assert "Helm hook" in reasons[("NetworkPolicy", "svc-test-egress")]


def test_a_changed_replica_count_is_reported(live: FakeLive) -> None:
    live.find("Deployment", "svc-backend")["spec"]["replicas"] = 5
    assert _run(live)[0] == {("Deployment", "svc-backend", "spec.replicas", "changed")}


def test_a_changed_image_is_reported(live: FakeLive) -> None:
    pod = live.find("Deployment", "svc-backend")["spec"]["template"]["spec"]
    pod["containers"][0]["image"] = "reg.example/svc-backend:hand-made"
    assert _run(live)[0] == {("Deployment", "svc-backend", f"{BACKEND}.image", "changed")}


def test_a_changed_and_an_added_env_var_are_reported_by_name(live: FakeLive) -> None:
    env = live.find("Deployment", "svc-backend")["spec"]["template"]["spec"]["containers"][0]["env"]
    env[0]["value"] = SENTINEL
    env.append({"name": "DEBUG", "value": "1"})
    assert _run(live)[0] == {
        ("Deployment", "svc-backend", f"{BACKEND}.env[LOG_LEVEL].value", "changed"),
        ("Deployment", "svc-backend", f"{BACKEND}.env[DEBUG]", "added"),
    }


def test_a_changed_and_a_removed_configmap_key_are_reported(live: FakeLive) -> None:
    data = live.find("ConfigMap", "svc-config")["data"]
    data["A"] = SENTINEL
    del data["B"]
    assert _run(live)[0] == {
        ("ConfigMap", "svc-config", "data.A", "changed"),
        ("ConfigMap", "svc-config", "data.B", "changed"),
    }


def test_a_deleted_object_is_reported_missing(live: FakeLive) -> None:
    live.objects.remove(live.find("PodDisruptionBudget", "svc-backend"))
    assert _run(live)[0] == {("PodDisruptionBudget", "svc-backend", "", "missing")}


def test_an_extra_object_with_the_release_label_is_reported_and_one_without_is_not(
    live: FakeLive,
) -> None:
    live.objects.append(_server(_obj("ConfigMap", "svc-hand-made", data={"x": "y"})))
    unlabelled = _server(_obj("ConfigMap", "someone-elses"))
    unlabelled["metadata"]["labels"] = {}
    live.objects.append(unlabelled)
    live.objects.append(_server(_obj("Job", "svc-hand-run", api="batch/v1")))
    assert _run(live)[0] == {
        ("ConfigMap", "svc-hand-made", "", "extra"),
        ("Job", "svc-hand-run", "", "extra"),
    }


def test_a_relabelled_object_is_found_by_name_and_its_label_reported(live: FakeLive) -> None:
    del live.find("ConfigMap", "svc-config")["metadata"]["labels"]["app.kubernetes.io/instance"]
    assert _run(live)[0] == {
        ("ConfigMap", "svc-config", "metadata.labels.app.kubernetes.io/instance", "changed")
    }


@pytest.mark.parametrize(
    ("count", "reported"), [(1, True), (2, False), (4, False), (0, True), (9, True)]
)
def test_a_workload_the_hpa_scales_is_checked_against_the_hpas_range(
    live: FakeLive, count: int, *, reported: bool
) -> None:
    live.find("Deployment", "svc-frontend")["spec"]["replicas"] = count
    want = {("Deployment", "svc-frontend", "spec.replicas", "outside the HPA's range")}
    assert _run(live)[0] == (want if reported else set())


def test_an_externalsecret_that_is_not_ready_is_reported(live: FakeLive) -> None:
    es = live.find("ExternalSecret", "svc-secret")
    es["status"]["conditions"][0].update(status="False", reason="SecretSyncedError")
    assert _run(live)[0] == {
        ("ExternalSecret", "svc-secret", "status.conditions[Ready]", "not ready")
    }


def test_a_secret_is_never_read_and_a_targeted_one_names_its_externalsecret(live: FakeLive) -> None:
    objs = [*rendered(), _obj("Secret", "svc-secret", stringData={"k": SENTINEL})]
    _, notes = drift.check(objs, REL, live)
    assert not [r for r in live.reads if r[1] == "Secret"]
    reasons = {n["name"]: n["reason"] for n in notes if n["kind"] == "Secret"}
    assert "ExternalSecret svc-secret is checked" in reasons["svc-secret"]


def test_an_unknown_kind_stops_the_check() -> None:
    with pytest.raises(LookupError, match="Widget"):
        drift.check([_obj("Widget", "w", api="example.com/v1")], REL, FakeLive([]))


@pytest.mark.parametrize(
    ("want", "have", "path", "equal"),
    [
        ("1000m", "1", "spec.resources.requests.cpu", True),
        ("0.5", "500m", "spec.resources.requests.cpu", True),
        ("1Gi", "1073741824", "spec.resources.limits.memory", True),
        ("1Gi", "1G", "spec.resources.limits.memory", False),
        ("1k", "1000", "data.SIZE", False),  # a quantity only under resources
        (8000, "8000", "spec.ports[0].port", True),
        (True, "true", "spec.x", False),
    ],
)
def test_scalars_compare_quantities_only_under_resources(
    want: object, have: object, path: str, *, equal: bool
) -> None:
    assert drift.same(want, have, path) is equal


def test_the_kubectl_resource_names_carry_the_version_and_group() -> None:
    assert drift.Kubectl.resource("v1", "ConfigMap") == "configmaps"
    assert drift.Kubectl.resource("apps/v1", "Deployment") == "deployments.v1.apps"
    assert (
        drift.Kubectl.resource("external-secrets.io/v1", "ExternalSecret")
        == "externalsecrets.v1.external-secrets.io"
    )
    assert "Secret" not in drift.RESOURCES


# ---- The command line, with a stub kubectl ------------------------------------------------------

STUB = textwrap.dedent(
    """\
    import json, sys
    args = sys.argv[1:]
    with open(sys.argv[0] + ".log", "a") as log:
        log.write(" ".join(args) + "\\n")
    objs = json.load(open(sys.argv[0] + ".json"))
    if "FORBIDDEN" in json.dumps(objs) and "deployments" in args[1]:
        sys.stderr.write("Error from server (Forbidden): deployments.apps is forbidden\\n")
        sys.exit(1)
    plural = args[1].split(".")[0]
    ns = args[args.index("-n") + 1]
    hits = [o for o in objs if o["plural"] == plural and o["metadata"]["namespace"] == ns]
    if "-l" in args:
        k, v = args[args.index("-l") + 1].split("=")
        hits = [o for o in hits if o["metadata"].get("labels", {}).get(k) == v]
        print(json.dumps({"items": hits}))
    else:
        hits = [o for o in hits if o["metadata"]["name"] == args[2]]
        print(json.dumps(hits[0]) if hits else "")
    """
)


def _cli(tmp_path: Path, objects: list[Obj]) -> tuple[int, Path, Path]:
    stub = tmp_path / "kubectl.py"
    stub.write_text(STUB)
    plural = drift.RESOURCES
    (tmp_path / "kubectl.py.json").write_text(
        json.dumps([{**o, "plural": plural[o["kind"]]} for o in objects])
    )
    manifest = tmp_path / "render.yaml"
    manifest.write_text(yaml.safe_dump_all(rendered()))
    out = tmp_path / "dev.json"
    argv = [
        "--rendered",
        str(manifest),
        "--release",
        REL,
        "--out",
        str(out),
        "--environment",
        "dev",
    ]
    rc = drift.main([*argv, "--kubectl", f"{sys.executable} {stub}"])
    return rc, out, tmp_path / "kubectl.py.log"


def test_the_command_writes_findings_without_values_and_only_gets_and_lists(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    objects = live_objects()
    cm = next(o for o in objects if o["metadata"]["name"] == "svc-config")
    cm["data"]["A"] = SENTINEL
    rc, out, log = _cli(tmp_path, objects)
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["environment"] == "dev"
    assert [(f["kind"], f["path"]) for f in report["findings"]] == [("ConfigMap", "data.A")]
    printed = capsys.readouterr().out
    assert "changed: ConfigMap `svc-dev/svc-config`, `data.A`" in printed
    for text in (out.read_text(), printed, summary.read_text()):
        assert SENTINEL not in text
    verbs = {line.split()[0] for line in log.read_text().splitlines()}
    assert verbs == {"get"}
    assert not [ln for ln in log.read_text().splitlines() if ln.split()[1].startswith("secrets")]


def test_a_read_the_identity_is_refused_fails_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    objects = live_objects()
    objects[0]["metadata"]["annotations"]["x"] = "FORBIDDEN"
    rc, out, _ = _cli(tmp_path, objects)
    assert rc == 1
    assert not out.exists()
    assert "::error::kubectl get deployments.v1.apps" in capsys.readouterr().err


def test_an_added_configmap_key_is_reported_at_its_path(live: FakeLive) -> None:
    """ConfigMap data and binaryData are closed maps: the app reads every key (envFrom)."""
    cm = live.find("ConfigMap", "svc-config")
    cm["data"]["HAND_MADE"] = SENTINEL
    cm["binaryData"] = {"blob": "AAAA"}
    assert _run(live)[0] == {
        ("ConfigMap", "svc-config", "data.HAND_MADE", "added"),
        ("ConfigMap", "svc-config", "binaryData.blob", "added"),
    }


def test_an_added_label_or_annotation_elsewhere_is_not_a_finding(live: FakeLive) -> None:
    """Only ConfigMap data is closed; other maps compare the render's keys only."""
    live.find("Deployment", "svc-backend")["spec"]["template"]["metadata"]["labels"]["x"] = "y"
    assert _run(live)[0] == set()


POD = "spec.template.spec"


@pytest.mark.parametrize(
    ("where", "key"),
    [
        ("container", "allowPrivilegeEscalation"),
        ("container", "privileged"),
        ("container", "readOnlyRootFilesystem"),
        ("container", "runAsNonRoot"),
        ("pod", "automountServiceAccountToken"),
        ("pod", "enableServiceLinks"),
    ],
)
def test_deleting_a_security_field_the_render_sets_is_reported(
    live: FakeLive, where: str, key: str
) -> None:
    pod = live.find("Deployment", "svc-backend")["spec"]["template"]["spec"]
    if where == "pod":
        del pod[key]
        path = f"{POD}.{key}"
    else:
        del pod["containers"][0]["securityContext"][key]
        path = f"{BACKEND}.securityContext.{key}"
    assert _run(live)[0] == {("Deployment", "svc-backend", path, "changed")}


def test_a_plain_false_the_server_drops_is_not_reported(live: FakeLive) -> None:
    """volumeMounts[].readOnly is a plain bool: false comes back omitted (the fixture's server)."""
    pod = live.find("Deployment", "svc-backend")["spec"]["template"]["spec"]
    assert "readOnly" not in pod["containers"][0]["volumeMounts"][0]
    assert _run(live)[0] == set()


@pytest.mark.parametrize(
    ("want", "have", "equal"),
    [(0.5, "500m", True), (0.25, "250m", True), (1.5, "1500m", True), (0.5, "600m", False)],
)
def test_a_float_quantity_compares_by_value(want: float, have: str, *, equal: bool) -> None:
    assert drift.same(want, have, "spec.resources.requests.cpu") is equal


def test_a_bool_is_never_a_quantity() -> None:
    yes = True
    assert drift.quantity(yes) is None
    assert drift.same(yes, "1", "spec.resources.requests.cpu") is False
