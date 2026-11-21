"""Where the backend serves its fixed endpoints: one definition for routes and exemptions."""

from __future__ import annotations

from typing import Final

API_PREFIX: Final[str] = "/api"
HEALTH_ROUTE: Final[str] = "/health"
LIVENESS_ROUTE: Final[str] = "/healthz"
METRICS_PATH: Final[str] = "/metrics"
#: The opt-in CSP report endpoint, under the versioned prefix (see ``api/routers/csp_report.py``).
CSP_REPORT_ROUTE: Final[str] = "/csp-report"


def exempt_paths(version: str) -> frozenset[str]:
    """The exact paths the rate limiter and the host check skip: the probes and the metrics.

    Kubernetes probes and Prometheus reach the Pod by its IP, and neither may be throttled. Exact
    paths, not suffixes: a route that merely ends in ``/health`` is an ordinary route.
    """
    base = f"{API_PREFIX}/{version}"
    return frozenset({f"{base}{HEALTH_ROUTE}", f"{base}{LIVENESS_ROUTE}", METRICS_PATH})
