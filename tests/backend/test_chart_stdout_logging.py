# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Logs go to stdout only: no collector tier, no log volume, no LOG_DIR, request log on."""

from __future__ import annotations

import os
import subprocess
from typing import Any

import pytest
import yaml

from tests.backend.test_chart_production_guard import CHART, find_helm, require_helm

pytestmark = pytest.mark.skipif(find_helm() is None and not os.environ.get("CI"), reason="no helm")


def _docs() -> list[dict[str, Any]]:
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    out = subprocess.run(
        [helm, "template", "svc", str(CHART), "-f", str(CHART / "values.dev.yaml")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _pods() -> list[dict[str, Any]]:
    return [d["spec"]["template"]["spec"] for d in _docs() if d["kind"] == "Deployment"]


def test_the_chart_ships_no_log_collector_tier() -> None:
    kinds = {d["kind"] for d in _docs()}
    assert "DaemonSet" not in kinds
    assert not [d for d in _docs() if "logging" in d["metadata"]["name"]]
    assert not list(CHART.glob("templates/*logging*"))
    values = (CHART / "values.yaml").read_text()
    assert "Suffix: logging" not in values
    assert "fluentd" not in values.lower()


def test_no_pod_has_a_log_volume_and_tmp_is_the_only_emptydir() -> None:
    pods = _pods()
    assert pods
    for pod in pods:
        assert [v["name"] for v in pod["volumes"] if "emptyDir" in v] == ["tmp"]
        for c in pod["containers"]:
            assert not [m for m in c["volumeMounts"] if "logs" in m["mountPath"]]
            assert c["securityContext"]["readOnlyRootFilesystem"] is True


def test_the_config_has_no_log_directory_and_the_request_log_is_on() -> None:
    cms = [d for d in _docs() if d["kind"] == "ConfigMap"]
    cm = next(d for d in cms if "LOG_REQUESTS" in d.get("data", {}))
    assert not [k for k in cm["data"] if k.startswith("LOG_") and k.endswith("_DIR")]
    assert cm["data"]["LOG_REQUESTS"] == "true"
