# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Liveness and startup are process-only; only readiness checks the database schema."""

from __future__ import annotations

import re

from tests.backend.test_chart_production_guard import CHART
from your_service_name.backend import paths
from your_service_name.utils.versioning_api import DEFAULT_API_VERSION

BASE = f"{paths.API_PREFIX}/{DEFAULT_API_VERSION}"


def _probe_path(probe: str) -> str:
    text = (CHART / "templates" / "backend-deployment.yaml").read_text(encoding="utf-8")
    match = re.search(rf"{probe}:\n\s+httpGet:\n\s+path: (\S+)", text)
    assert match, probe
    return match.group(1)


def test_liveness_uses_the_process_only_path() -> None:
    assert _probe_path("livenessProbe") == f"{BASE}{paths.LIVENESS_ROUTE}"


def test_only_readiness_checks_the_schema() -> None:
    assert _probe_path("readinessProbe") == f"{BASE}{paths.HEALTH_ROUTE}"


def test_startup_uses_the_process_only_path() -> None:
    """The Job may outlast the startup budget; a startup probe that waits for the schema would
    kill every new Pod during a long migration."""
    assert _probe_path("startupProbe") == f"{BASE}{paths.LIVENESS_ROUTE}"
