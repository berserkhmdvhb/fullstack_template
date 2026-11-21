# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The backend's keep-alive must outlive the gateway's upstream idle timeout.

If Uvicorn closes an idle connection first, the proxy can reuse it in the same instant and
return a 502. The launcher owns the backend value; the chart owns the Envoy (WAF) one.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

import pytest
import yaml

from tests.backend.test_chart_production_guard import CHART, ROOT, find_helm, require_helm

LAUNCHER = ROOT / "infra" / "backend" / "run_backend.sh"
DEFAULT = re.compile(r'BACKEND_KEEPALIVE_TIMEOUT="\$\{BACKEND_KEEPALIVE_TIMEOUT:-(\d+)\}"')


def _backend_keepalive() -> int:
    match = DEFAULT.search(LAUNCHER.read_text(encoding="utf-8"))
    assert match, "the launcher no longer defaults BACKEND_KEEPALIVE_TIMEOUT"
    return int(match.group(1))


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CHART / name).read_text(encoding="utf-8")) or {}


def _gateway_idle() -> int:
    idle = _load("values.yaml")["waf"]["upstreamIdleTimeoutSeconds"]
    assert isinstance(idle, int)
    return idle


def test_the_gateway_idle_timeout_is_shorter_than_the_backend_keep_alive() -> None:
    assert _gateway_idle() < _backend_keepalive()


@pytest.mark.parametrize("env", ["dev", "qa", "production", "local"])
def test_no_environment_sets_a_keep_alive_below_the_gateway_timeout(env: str) -> None:
    """A BACKEND_KEEPALIVE_TIMEOUT in any values file still has to clear the gateway's."""
    config = {**_load("values.yaml")["config"], **_load(f"values.{env}.yaml").get("config", {})}
    assert int(config.get("BACKEND_KEEPALIVE_TIMEOUT", _backend_keepalive())) > _gateway_idle()


def test_the_rendered_envoy_cluster_carries_the_idle_timeout() -> None:
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    out = subprocess.run(
        [helm, "template", "svc", str(CHART), "--set", "waf.enabled=true"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert f"idle_timeout: {_gateway_idle()}s" in out.stdout
