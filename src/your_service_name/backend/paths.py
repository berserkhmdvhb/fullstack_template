"""Where the backend serves its fixed endpoints: one definition for routes and exemptions."""

from __future__ import annotations

from typing import Final

API_PREFIX: Final[str] = "/api"
HEALTH_ROUTE: Final[str] = "/health"
LIVENESS_ROUTE: Final[str] = "/healthz"
#: The opt-in CSP report endpoint, under the versioned prefix (see ``api/routers/csp_report.py``).
CSP_REPORT_ROUTE: Final[str] = "/csp-report"


def csp_report_path(version: str) -> str:
    """The CSP report endpoint path for an API ``version``, e.g. ``/api/v1/csp-report``."""
    return f"{API_PREFIX}/{version}{CSP_REPORT_ROUTE}"


def exempt_paths(version: str) -> frozenset[str]:
    """The exact paths the rate limiter and the host check skip: the probes.

    Kubernetes probes reach the Pod by its IP and may not be throttled. The metrics are not an API
    route: they have their own port (``backend/serve.py``). Exact paths, not suffixes: a route that
    merely ends in ``/health`` is an ordinary route.
    """
    base = f"{API_PREFIX}/{version}"
    return frozenset({f"{base}{HEALTH_ROUTE}", f"{base}{LIVENESS_ROUTE}"})


def rate_limit_exempt_paths(version: str) -> frozenset[str]:
    """The paths the default rate limit skips: the probes and the CSP report route.

    The report route has its own limit (``CSP_REPORT_RATE_LIMIT``), so browser reports never use up
    a user's API budget.
    """
    return exempt_paths(version) | {csp_report_path(version)}
