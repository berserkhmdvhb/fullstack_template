# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The metrics are served on their own port, with the names the dashboards already use."""

from __future__ import annotations

import re
import socket
import urllib.request

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY, generate_latest, start_http_server

from your_service_name.backend.main import LATENCY_BUCKETS, create_app


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_the_metrics_server_answers_on_its_port_and_the_api_app_does_not() -> None:
    # The lifespan runs (the engine exists), so the pool gauges have a pool to read.
    with TestClient(create_app()) as client:
        assert client.get("/api/v1/health").status_code in {200, 503}
        assert client.get("/metrics").status_code == 404  # noqa: PLR2004

        port = _free_port()
        server, thread = start_http_server(port, addr="127.0.0.1")
        try:
            url = f"http://127.0.0.1:{port}/metrics"
            with urllib.request.urlopen(url, timeout=5) as res:
                body = res.read().decode()
        finally:
            server.shutdown()
            thread.join(timeout=5)
    # The instrumentation's names are unchanged: the dashboards and the alert rules use them.
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    # The rate-limit counters and the pool gauges the SLOs and dashboards use.
    assert "rate_limit_rejections_total" in body
    assert "rate_limit_storage_failures_total" in body
    for name in (
        "db_pool_size",
        "db_pool_checked_out",
        "db_pool_checked_in",
        "db_pool_overflow",
        "db_pool_max_overflow",
    ):
        assert name in body


def test_the_labelled_latency_histogram_has_the_slo_buckets() -> None:
    """The latency SLO's threshold is a bucket bound: the served `le` set is LATENCY_BUCKETS."""
    with TestClient(create_app()) as client:
        client.get("/api/v1/healthz")
    body = generate_latest(REGISTRY).decode()
    bounds = set(
        re.findall(r'^http_request_duration_seconds_bucket\{[^}]*le="([^"]+)"', body, re.MULTILINE)
    )
    assert bounds == {*(str(b) for b in LATENCY_BUCKETS), "+Inf"}
    assert LATENCY_BUCKETS == (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
