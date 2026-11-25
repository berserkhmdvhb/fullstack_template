#!/usr/bin/env python3
"""Compare a release's render with the live objects of the cluster, read-only.

Usage: drift.py --rendered FILE --release NAME --out FILE [--environment ENV] [--kubectl CMD]

FILE is `helm template` output (scripts/ci/deploy-env.sh with DEPLOY_RENDER_ONLY). The live
objects are read with kubectl (`get` and `list` only, as the drift identity), never a Secret.

Only the fields the render sets are compared, recursively: a field the server or a controller adds
(status, defaults, managedFields, resourceVersion, uid, generation, creationTimestamp, a label or
annotation the chart does not set) never counts. Lists are compared by position, or by `name`
when every item has one (containers, env, ports, volumes: Kubernetes merges those by name), and an
item the render lacks is reported then. Reported, each with kind, namespace, name and field path:
  * changed   a field the render sets differs, or is gone
  * missing   a rendered object does not exist
  * extra     an object of a rendered kind carries the release's instance label but is not rendered
              (not one a controller owns, a Helm hook, an earlier revision's migration Job or a
              StatefulSet's claim)
  * not ready an ExternalSecret whose Ready condition is not True
  * replicas  a workload the HPA scales (the render sets no replicas) is outside the HPA's range
Not compared, each with its reason: Secrets (never read; the ExternalSecret that writes one is
checked instead), Jobs (a run: named by the Helm revision and removed after their TTL) and Helm
hooks. Values are never printed, only paths, so a report never leaks what a field holds.

Writes FILE as JSON ({"environment", "release", "findings": [...], "notes": [...]}) and a summary
to stdout and GITHUB_STEP_SUMMARY. Findings exit 0; a failing read (a kind the identity may not
list, an unknown kind) exits 1.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

import yaml

INSTANCE = "app.kubernetes.io/instance"
HOOK = "helm.sh/hook"
# Kind -> resource, for kubectl. Every kind the chart renders but Secret; the drift Role grants
# get and list on these (infra/k8s/bootstrap/templates/drift-rbac.yaml; a test keeps them equal).
RESOURCES = {
    "ConfigMap": "configmaps",
    "Service": "services",
    "ServiceAccount": "serviceaccounts",
    "PersistentVolumeClaim": "persistentvolumeclaims",
    "Deployment": "deployments",
    "StatefulSet": "statefulsets",
    "Job": "jobs",
    "CronJob": "cronjobs",
    "HorizontalPodAutoscaler": "horizontalpodautoscalers",
    "PodDisruptionBudget": "poddisruptionbudgets",
    "NetworkPolicy": "networkpolicies",
    "Ingress": "ingresses",
    "HTTPRoute": "httproutes",
    "HealthCheckPolicy": "healthcheckpolicies",
    "GCPBackendPolicy": "gcpbackendpolicies",
    "ServiceMonitor": "servicemonitors",
    "PodMonitoring": "podmonitorings",
    "Rules": "rules",
    "PrometheusRule": "prometheusrules",
    "ExternalSecret": "externalsecrets",
}
SERVER_META = {"managedFields", "resourceVersion", "uid", "generation", "creationTimestamp"}
EMPTY: tuple[object, ...] = (None, "", {}, [], False, 0)  # dropped by the server when set
QUANTITY = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))(Ki|Mi|Gi|Ti|Pi|Ei|n|u|m|k|M|G|T|P|E)?$")
SCALE = {
    **{s: Decimal(1024) ** (i + 1) for i, s in enumerate(("Ki", "Mi", "Gi", "Ti", "Pi", "Ei"))},
    **{s: Decimal(10) ** e for s, e in (("n", -9), ("u", -6), ("m", -3), ("", 0), ("k", 3))},
    **{s: Decimal(10) ** e for s, e in (("M", 6), ("G", 9), ("T", 12), ("P", 15), ("E", 18))},
}
REVISION = re.compile(r"-r\d+$")

Obj = dict[str, Any]


class Live(Protocol):
    """The cluster, read-only."""

    def list(self, api_version: str, kind: str, namespace: str, selector: str) -> list[Obj]: ...

    def get(self, api_version: str, kind: str, namespace: str, name: str) -> Obj | None: ...


def quantity(value: object) -> Decimal | None:
    m = QUANTITY.match(str(value)) if isinstance(value, str | int) else None
    try:
        return Decimal(m[1]) * SCALE[m[2] or ""] if m else None
    except InvalidOperation:
        return None


def same(want: object, have: object, path: str) -> bool:
    if want == have:
        return True
    if isinstance(want, bool) or isinstance(have, bool):
        return False
    if ".resources." in f"{path}." and quantity(want) is not None:
        return quantity(want) == quantity(have)
    return isinstance(want, int | float | str) and str(want) == str(have)


def _named(*lists: list[Any]) -> bool:
    return all(isinstance(i, dict) and "name" in i for items in lists for i in items)


def _diff_list(want: list[Any], have: object, path: str) -> list[str]:
    if not isinstance(have, list):
        return [] if not want and have is None else [path]
    if want and _named(want, have):
        live = {i["name"]: i for i in have}
        names = {w["name"] for w in want}
        out = [f"{path}[{i['name']}]" for i in have if i["name"] not in names]
        for item in want:
            sub = f"{path}[{item['name']}]"
            out += diff(item, live[item["name"]], sub) if item["name"] in live else [sub]
        return sorted(out)
    if len(want) != len(have):
        return [path]
    pairs = enumerate(zip(want, have, strict=True))
    return [p for i, (w, h) in pairs for p in diff(w, h, f"{path}[{i}]")]


def diff(want: object, have: object, path: str = "") -> list[str]:
    """The paths under `path` where `have` differs from what `want` sets."""
    if isinstance(want, list):
        return _diff_list(want, have, path)
    if not isinstance(want, dict):
        return [] if same(want, have, path) else [path]
    if not isinstance(have, dict):
        return [path]
    out: list[str] = []
    for key, value in want.items():
        sub = f"{path}.{key}" if path else key
        if key not in have or have[key] is None:
            out += [] if value in EMPTY else [sub]
        else:
            out += diff(value, have[key], sub)
    return out


def compare(rendered: Obj, live: Obj) -> list[str]:
    """The changed paths of one object: the render's fields only, never status or server data."""
    want = {k: v for k, v in rendered.items() if k not in {"status", "apiVersion", "kind"}}
    meta = {k: v for k, v in (want.get("metadata") or {}).items() if k not in SERVER_META}
    return diff({**want, "metadata": meta}, live)


def _key(obj: Obj) -> tuple[str, str, str]:
    meta = obj.get("metadata") or {}
    return str(obj.get("kind")), str(meta.get("namespace", "")), str(meta.get("name"))


def _finding(key: tuple[str, str, str], path: str, change: str) -> dict[str, str]:
    return {"kind": key[0], "namespace": key[1], "name": key[2], "path": path, "change": change}


def _note(key: tuple[str, str, str], reason: str) -> dict[str, str]:
    return {"kind": key[0], "namespace": key[1], "name": key[2], "reason": reason}


def _expected_extra(obj: Obj, rendered: list[Obj]) -> bool:
    """A live object of the release that the render does not list, by design."""
    meta = obj.get("metadata") or {}
    if meta.get("ownerReferences") or HOOK in (meta.get("annotations") or {}):
        return True
    kind, ns, name = _key(obj)
    same_ns = [r for r in rendered if _key(r)[1] == ns]
    if kind == "Job":  # the migration Job of an earlier revision, kept by its resource policy
        return any(
            _key(r)[0] == "Job" and REVISION.sub("", _key(r)[2]) == REVISION.sub("", name)
            for r in same_ns
        )
    if kind == "PersistentVolumeClaim":  # a StatefulSet's claim: <template>-<statefulset>-<ordinal>
        return any(
            re.fullmatch(rf"{re.escape(t['metadata']['name'])}-{re.escape(_key(r)[2])}-\d+", name)
            for r in same_ns
            if _key(r)[0] == "StatefulSet"
            for t in r.get("spec", {}).get("volumeClaimTemplates", [])
        )
    return False


def _secret_reason(secret: Obj, rendered: list[Obj]) -> str:
    _, ns, name = _key(secret)
    for es in rendered:
        target = (es.get("spec", {}).get("target") or {}).get("name") or _key(es)[2]
        if _key(es)[0] == "ExternalSecret" and _key(es)[1] == ns and target == name:
            return f"not read (no access to Secrets); its ExternalSecret {_key(es)[2]} is checked"
    return "not read: the drift identity has no access to Secrets (the deploy writes it)"


def _replicas(rendered: list[Obj], live: dict[tuple[str, str, str], Obj]) -> list[dict[str, str]]:
    """A workload the HPA scales (no replicas rendered) outside the HPA's range."""
    found = []
    by_key = {_key(r): r for r in rendered}
    for hpa in (r for r in rendered if r.get("kind") == "HorizontalPodAutoscaler"):
        spec = hpa.get("spec", {})
        target = (spec["scaleTargetRef"]["kind"], _key(hpa)[1], spec["scaleTargetRef"]["name"])
        workload, have = by_key.get(target), live.get(target)
        if not workload or not have or "replicas" in workload.get("spec", {}):
            continue
        count = have.get("spec", {}).get("replicas", 1)
        if not spec.get("minReplicas", 1) <= count <= spec["maxReplicas"]:
            found.append(_finding(target, "spec.replicas", "outside the HPA's range"))
    return found


JOB = "not compared: a Job is a run, named by the Helm revision and removed after its TTL"
HOOK_NOTE = "not compared: a Helm hook, run by Helm and not kept"


def _split(rendered: list[Obj]) -> tuple[list[Obj], list[dict[str, str]]]:
    """(the objects to compare, notes on those that are not)."""
    compared: list[Obj] = []
    notes: list[dict[str, str]] = []
    for obj in rendered:
        key = _key(obj)
        if key[0] == "Secret":
            notes.append(_note(key, _secret_reason(obj, rendered)))
            continue
        if HOOK in (obj["metadata"].get("annotations") or {}):
            notes.append(_note(key, HOOK_NOTE))
            continue
        if key[0] not in RESOURCES:
            msg = (
                f"{key[0]}: a kind the drift check does not know; add it to RESOURCES and the Role"
            )
            raise LookupError(msg)
        if key[0] == "Job":
            notes.append(_note(key, JOB))
        compared.append(obj)
    return compared, notes


def _ready(have: Obj) -> bool:
    conditions = (have.get("status") or {}).get("conditions") or []
    return any(c.get("type") == "Ready" and c.get("status") == "True" for c in conditions)


def check(
    rendered: list[Obj], release: str, live: Live
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(findings, notes) for one release."""
    compared, notes = _split(rendered)
    findings: list[dict[str, str]] = []
    selector = f"{INSTANCE}={release}"
    seen: dict[tuple[str, str, str], Obj] = {}
    for api_version, kind, ns in sorted({(o["apiVersion"], *_key(o)[:2]) for o in compared}):
        for obj in live.list(api_version, kind, ns, selector):
            seen[_key({**obj, "kind": kind})] = obj
    for obj in (o for o in compared if o["kind"] != "Job"):
        key = _key(obj)
        have = seen.get(key) or live.get(obj["apiVersion"], *key)
        if have is None:
            findings.append(_finding(key, "", "missing"))
            continue
        seen[key] = have
        findings += [_finding(key, p, "changed") for p in compare(obj, have)]
        if key[0] == "ExternalSecret" and not _ready(have):
            findings.append(_finding(key, "status.conditions[Ready]", "not ready"))
    wanted = {_key(o) for o in compared}
    for key, obj in sorted(seen.items()):
        if key not in wanted and not _expected_extra({**obj, "kind": key[0]}, compared):
            findings.append(_finding(key, "", "extra"))
    findings += _replicas(compared, seen)
    return findings, notes


class Kubectl:
    """The live objects through kubectl, `get` and `list` only."""

    def __init__(self, cmd: list[str]) -> None:
        self.cmd = cmd

    @staticmethod
    def resource(api_version: str, kind: str) -> str:
        group, _, version = api_version.rpartition("/")
        return f"{RESOURCES[kind]}.{version}.{group}" if group else RESOURCES[kind]

    def _json(self, args: list[str]) -> Obj | None:
        cmd = [*self.cmd, *args, "-o", "json"]
        done = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
        if done.returncode:
            msg = f"kubectl {' '.join(args[:4])} failed: {done.stderr.strip()[:300]}"
            raise RuntimeError(msg)
        return json.loads(done.stdout) if done.stdout.strip() else None

    def list(self, api_version: str, kind: str, namespace: str, selector: str) -> list[Obj]:
        got = self._json(["get", self.resource(api_version, kind), "-n", namespace, "-l", selector])
        return list((got or {}).get("items", []))

    def get(self, api_version: str, kind: str, namespace: str, name: str) -> Obj | None:
        res = self.resource(api_version, kind)
        return self._json(["get", res, name, "-n", namespace, "--ignore-not-found"])


def summary(env: str, findings: list[dict[str, str]], notes: list[dict[str, str]]) -> str:
    lines = [f"### Live drift: {env}", ""]
    for f in findings:
        where = f"`{f['path']}`" if f["path"] else "the object"
        lines.append(f"- {f['change']}: {f['kind']} `{f['namespace']}/{f['name']}`, {where}")
    if not findings:
        lines.append("- no drift: the live objects match the render")
    lines += [f"- {n['kind']} `{n['namespace']}/{n['name']}`: {n['reason']}" for n in notes]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rendered", required=True, type=Path)
    parser.add_argument("--release", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--environment", default="")
    parser.add_argument(
        "--kubectl", default="kubectl", help="the kubectl command (split like a shell)"
    )
    args = parser.parse_args(argv)
    rendered = [d for d in yaml.safe_load_all(args.rendered.read_text()) if d]
    try:
        findings, notes = check(rendered, args.release, Kubectl(shlex.split(args.kubectl)))
    except (LookupError, RuntimeError) as err:
        sys.stderr.write(f"::error::{err}\n")
        return 1
    env = args.environment or args.release
    report = {"environment": env, "release": args.release, "findings": findings, "notes": notes}
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    text = summary(env, findings, notes)
    sys.stdout.write(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
