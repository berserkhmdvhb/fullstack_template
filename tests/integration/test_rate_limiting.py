# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Integration tests for rate-limit enforcement on the running app.

Verifies that the rate-limit middleware actually throttles requests (the earlier
template configured a limiter that was never enforced, so the limit was a no-op)
and that liveness/readiness/metrics paths stay exempt.

Marked with ``@pytest.mark.integration`` — runs the full ``create_app`` stack.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI, status

from your_service_name.backend.main import create_app

API_BASE: str = "/api/v1"
ECHO_PATH: str = f"{API_BASE}/echo"
HEALTH_PATH: str = f"{API_BASE}/health"
METRICS_PATH: str = "/metrics"


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[httpx.AsyncClient]:
    """Full app under a low default limit, with lifespan (DB + middleware)."""
    monkeypatch.setenv("RATE_LIMIT_DEFAULT", "3/minute")
    app: FastAPI = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.integration
@pytest.mark.asyncio
async def test_burst_beyond_default_limit_is_429(client: httpx.AsyncClient) -> None:
    """The (limit+1)-th request in the window is rejected with 429."""
    # First three requests (the limit) are served.
    for _ in range(3):
        res = await client.post(ECHO_PATH, json={"text": "hi"})
        assert res.status_code == status.HTTP_201_CREATED

    # The fourth exceeds the limit.
    res = await client.post(ECHO_PATH, json={"text": "hi"})
    assert res.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert res.headers.get("Retry-After") is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_probes_exempt_from_rate_limit(client: httpx.AsyncClient) -> None:
    """Health and metrics stay reachable even after the limit is exhausted."""
    # Exhaust the limit on a normal endpoint.
    for _ in range(3):
        assert (
            await client.post(ECHO_PATH, json={"text": "hi"})
        ).status_code == status.HTTP_201_CREATED
    assert (
        await client.post(ECHO_PATH, json={"text": "hi"})
    ).status_code == status.HTTP_429_TOO_MANY_REQUESTS

    # Nested /api/v1/health and the top-level /metrics are exempt.
    health = await client.get(HEALTH_PATH)
    assert health.status_code == status.HTTP_200_OK
    metrics = await client.get(METRICS_PATH)
    assert metrics.status_code == status.HTTP_200_OK
