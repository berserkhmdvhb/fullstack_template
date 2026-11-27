# your-service-name — adaptable service template (see wiki/getting-started.md)
"""deploy/cloudflared: the manifests keep what the chart and the guide rely on."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "deploy" / "cloudflared"
MIN_REPLICAS = 2


def load(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((DIR / name).read_text(encoding="utf-8"))
    return loaded


def test_the_image_is_pinned_by_digest() -> None:
    pod = load("deployment.yaml")["spec"]["template"]["spec"]
    for container in pod["containers"]:
        assert re.fullmatch(
            r"cloudflare/cloudflared:[0-9.]+@sha256:[0-9a-f]{64}", container["image"]
        )


def test_the_namespace_is_the_one_the_chart_admits_in_tunnel_mode() -> None:
    values = yaml.safe_load(
        (ROOT / "charts" / "your-service-name" / "values.yaml").read_text("utf-8")
    )
    assert values["edge"]["namespaceSelector"] == {}, (
        "the chart's default selector is the cloudflared namespace"
    )
    assert load("namespace.yaml")["metadata"]["name"] == "cloudflared"
    for name in ("deployment.yaml", "pdb.yaml", "networkpolicy.yaml", "externalsecret.yaml"):
        assert load(name)["metadata"]["namespace"] == "cloudflared", name


def test_the_connector_is_restricted_and_has_no_api_token() -> None:
    deployment = load("deployment.yaml")
    pod = deployment["spec"]["template"]["spec"]
    assert (
        load("namespace.yaml")["metadata"]["labels"]["pod-security.kubernetes.io/enforce"]
        == "restricted"
    )
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["priorityClassName"] == load("priorityclass.yaml")["metadata"]["name"]
    security = pod["containers"][0]["securityContext"]
    assert security["readOnlyRootFilesystem"] is True
    assert security["allowPrivilegeEscalation"] is False
    assert security["capabilities"] == {"drop": ["ALL"]}
    assert deployment["spec"]["replicas"] >= MIN_REPLICAS


def test_the_token_comes_from_the_secret_the_external_secret_writes() -> None:
    external = load("externalsecret.yaml")
    ref = load("deployment.yaml")["spec"]["template"]["spec"]["containers"][0]["env"][0]
    key = ref["valueFrom"]["secretKeyRef"]
    assert external["spec"]["target"]["name"] == key["name"]
    assert external["spec"]["data"][0]["secretKey"] == key["key"]
    assert external["spec"]["secretStoreRef"] == {"name": "secret-manager", "kind": "SecretStore"}
    assert external["spec"]["data"][0]["remoteRef"]["key"].startswith("cloudflared-")


def test_nothing_may_connect_to_the_connector() -> None:
    policy = load("networkpolicy.yaml")["spec"]
    assert policy["ingress"] == []
    assert set(policy["policyTypes"]) == {"Ingress", "Egress"}
