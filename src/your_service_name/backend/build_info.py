# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
The service's own info metric: a gauge named with the service name, value 1, from import.

The dashboards' ``cluster`` and ``namespace`` variables list its labels. A series every service
built from the template exports (a limiter counter, say) would list another service's namespaces
under this one's title when two share a Prometheus; this name is rewritten per service by the adapt
script (``your_service_name`` is the placeholder, so it stays a valid metric name).
"""

from __future__ import annotations

from typing import Final

from prometheus_client import Gauge

from your_service_name import __version__

#: The one source of the name (the dashboards' test reads it).
BUILD_INFO_METRIC: Final = "your_service_name_build_info"

#: One sample, version label only (bounded cardinality); set at import, not on first request.
BUILD_INFO = Gauge(
    BUILD_INFO_METRIC,
    "Constant 1; the version label is the package version (from the Git tag).",
    ["version"],
)
BUILD_INFO.labels(version=__version__).set(1)
