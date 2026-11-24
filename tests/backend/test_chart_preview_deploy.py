# your-service-name — adaptable service template (see wiki/getting-started.md)
"""A pull request preview renders as preview.yml deploys it: values.preview.yaml, the hosts and the
digests scripts/ci/deploy-env.sh passes, and the placeholder guard on. The only item the guard may
name is the owner's one documented edit (the Gateway); the database password comes from the Secrets
the bootstrap creates (scripts/k8s-bootstrap.sh preview --create-secrets), never from values."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from tests.backend.test_chart_production_guard import CHART, ROOT, find_helm, require_helm

if TYPE_CHECKING:
    from pathlib import Path

RELEASE = "your-service-name-pr-1"
NAMESPACE = "your-service-name-preview"
PREVIEW = CHART / "values.preview.yaml"
DIGEST = "sha256:" + "b" * 64
GATEWAY_ITEM = "edge.gateway.parentRefs names the placeholder Gateway"


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def _deploy_values(tmp_path: Path) -> list[str]:
    """The files deploy-env.sh layers over values.preview.yaml: the release's hosts (under the
    domain `invalid` when PREVIEW_DOMAIN is unset) and the images by digest (the WAF has none)."""
    hosts = tmp_path / "preview-hosts.json"
    hosts.write_text(
        json.dumps(
            {
                "edge": {
                    "hosts": {
                        s: f"{RELEASE}{x}.invalid"
                        for s, x in (("root", ""), ("api", "-api"), ("app", "-app"))
                    }
                }
            }
        )
    )
    images = tmp_path / "image-values.json"
    images.write_text(
        json.dumps(
            {
                "images": {
                    i: {"registry": "ghcr.io", "repository": f"o/r/{i}", "tag": "dev"}
                    | ({} if i == "waf" else {"digest": DIGEST})
                    for i in ("backend", "frontend", "waf")
                }
            }
        )
    )
    return ["-f", str(PREVIEW), "-f", str(hosts), "-f", str(images)]


def _render(helm: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            helm,
            "template",
            RELEASE,
            str(CHART),
            *args,
            "--namespace",
            NAMESPACE,
            "--set",
            "guards.rejectPlaceholders=true",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_guard_names_only_the_documented_gateway_edit(helm: str, tmp_path: Path) -> None:
    result = _render(helm, *_deploy_values(tmp_path))
    assert result.returncode != 0
    items = re.findall(r"^  - (.*)$", result.stderr, flags=re.MULTILINE)
    assert len(items) == 1, items
    assert items[0].startswith(GATEWAY_ITEM), items
    # The one edit "Turn them on" documents.
    turn_on = (ROOT / "wiki" / "ci-cd.md").read_text().split("**Turn them on**", 1)[1]
    assert "`edge.gateway.parentRefs`" in turn_on.split("\n2. ", 1)[0]


def test_with_the_gateway_it_renders_and_takes_the_bootstraps_secrets(
    helm: str, tmp_path: Path
) -> None:
    gateway = tmp_path / "gateway.yaml"
    gateway.write_text("edge:\n  gateway:\n    parentRefs:\n      - {name: gw, namespace: gw-ns}\n")
    result = _render(helm, *_deploy_values(tmp_path), "-f", str(gateway))
    assert result.returncode == 0, result.stderr
    docs: list[dict[str, Any]] = [d for d in yaml.safe_load_all(result.stdout) if d]
    values = yaml.safe_load(PREVIEW.read_text())
    app, db = values["secrets"]["existingSecret"], values["postgresql"]["existingSecret"]
    assert app
    assert db
    assert not [
        d for d in docs if d["kind"] == "Secret" and "DB_PASSWORD" in d.get("stringData", {})
    ]
    (backend,) = [
        d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-backend")
    ]
    env_from = backend["spec"]["template"]["spec"]["containers"][0]["envFrom"]
    assert {"secretRef": {"name": app}} in env_from
    (pg,) = [d for d in docs if d["kind"] == "StatefulSet"]
    env = {e["name"]: e for e in pg["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"] == db
