# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Liveness and startup are process-only; only readiness checks the database schema."""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any

import yaml

from tests.backend.test_chart_pod_security import pod_specs, render
from tests.backend.test_chart_production_guard import find_helm, require_helm
from your_service_name.backend import paths
from your_service_name.utils.versioning_api import DEFAULT_API_VERSION

BASE = f"{paths.API_PREFIX}/{DEFAULT_API_VERSION}"
ROOT = Path(__file__).resolve().parents[2]


@functools.cache
def _containers() -> dict[str, dict[str, Any]]:
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    pods = pod_specs(render(helm, "values.yaml"))
    return {
        n.split("-")[-1]: spec[0]["containers"][0] for n, spec in pods.items() if "Deployment/" in n
    }


def _probe_path(probe: str, template: str = "backend-deployment.yaml") -> str:
    """The rendered path: the deployments take it from the chart's helpers, so the helm render (not
    the template text) is what the probe uses."""
    return str(_containers()[template.removesuffix("-deployment.yaml")][probe]["httpGet"]["path"])


def test_liveness_uses_the_process_only_path() -> None:
    assert _probe_path("livenessProbe") == f"{BASE}{paths.LIVENESS_ROUTE}"


def test_only_readiness_checks_the_schema() -> None:
    assert _probe_path("readinessProbe") == f"{BASE}{paths.HEALTH_ROUTE}"


def test_startup_uses_the_process_only_path() -> None:
    """The Job may outlast the startup budget; a startup probe that waits for the schema would
    kill every new Pod during a long migration."""
    assert _probe_path("startupProbe") == f"{BASE}{paths.LIVENESS_ROUTE}"


def test_the_frontend_probes_use_its_own_health_route() -> None:
    """The home page renders per request and proxies nothing; /api/healthz is process-only."""
    for probe in ("startupProbe", "readinessProbe", "livenessProbe"):
        assert _probe_path(probe, "frontend-deployment.yaml") == "/api/healthz", probe


def test_rebuild_and_roll_checks_each_component_on_its_readiness_path() -> None:
    """scripts/services.yaml feeds rebuild-and-roll.sh's in-pod check: it must hit the path the
    chart's readiness probe uses, not a page that renders server-side."""
    services = yaml.safe_load((ROOT / "scripts/services.yaml").read_text())["services"]
    paths_by_key = {s["key"]: s["health_path"] for s in services}
    for key in ("backend", "frontend"):
        assert paths_by_key[key] == _probe_path("readinessProbe", f"{key}-deployment.yaml"), key
