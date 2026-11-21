# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The probe and metrics paths the limiter and the host check skip are the ones the chart uses."""

from __future__ import annotations

import re

from tests.backend.test_chart_production_guard import CHART
from your_service_name.backend import paths
from your_service_name.utils.versioning_api import DEFAULT_API_VERSION


def _paths(template: str) -> list[str]:
    text = (CHART / "templates" / template).read_text(encoding="utf-8")
    return re.findall(r"^\s*path:\s*(/\S*)\s*$", text, re.MULTILINE)


def test_the_chart_probe_paths_are_exempt() -> None:
    probes = _paths("backend-deployment.yaml")
    assert len(probes) >= 3, probes  # startup, readiness, liveness  # noqa: PLR2004
    assert set(probes) <= paths.exempt_paths(DEFAULT_API_VERSION)


def test_the_chart_metrics_path_is_exempt() -> None:
    scraped = _paths("servicemonitor.yaml")
    assert scraped == ["/metrics"]
    assert set(scraped) <= paths.exempt_paths(DEFAULT_API_VERSION)


def test_only_exact_paths_are_exempt() -> None:
    exempt = paths.exempt_paths(DEFAULT_API_VERSION)
    assert "/api/v1/items/metrics" not in exempt
    assert "/x/health" not in exempt
