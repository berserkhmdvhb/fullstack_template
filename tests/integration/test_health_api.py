# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Integration tests for Health API endpoints.

Covers:
- GET /health: returns ok when DB initialized
- GET /health: returns degraded when not initialized
- GET /healthz: always returns ok
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Final

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI, status

from your_service_name.backend.main import create_app

API_BASE: Final[str] = "/api/v1"
HEALTH_PATH: Final[str] = f"{API_BASE}/health"
HEALTHZ_PATH: Final[str] = f"{API_BASE}/healthz"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    """Create a fresh FastAPI app with lifespan (runs DB migrations)."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client with lifespan manager."""
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_ok_when_initialized(client: httpx.AsyncClient) -> None:
    """GET /health should return ok when the DB is reachable and migrated."""
    res = await client.get(HEALTH_PATH)

    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["status"] == "ok"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_healthz_always_ok(client: httpx.AsyncClient) -> None:
    """GET /healthz should always return ok (no DB check)."""
    res = await client.get(HEALTHZ_PATH)

    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["status"] == "ok"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_degraded_when_db_unavailable(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    GET /health should return HTTP 503 (not 200) when the DB tier is not ready.

    This is the readiness-probe contract: a pod whose database is unavailable
    must be pulled from the Service rotation rather than silently serving 5xx
    behind a green health check (seen in an end-to-end run).

    We force the shared engine module into the "not initialized" state. This
    client intentionally skips the lifespan, and monkeypatch restores the prior
    globals afterwards; the other tests re-initialize via their lifespan-backed
    client fixture, so the assertion is order-independent.
    """
    import your_service_name.backend.db.engine as engine_module  # noqa: PLC0415 — scoped: force not-ready state for this probe test

    monkeypatch.setattr(engine_module, "_db_initialized", False)
    monkeypatch.setattr(engine_module, "_engine", None)
    monkeypatch.setattr(engine_module, "_session_factory", None)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get(HEALTH_PATH)

    assert res.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert res.json() == {"status": "degraded", "reason": "database_unreachable"}


# ---------------------------------------------------------------------------
# Tests: Health endpoint structure
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_health_response_structure(client: httpx.AsyncClient) -> None:
    """Health response should have the exact HealthStatus shape — the single
    ``status`` key (HealthStatus forbids extra fields) and a value in the
    Literal["ok", "degraded"] domain, not just any string."""
    res = await client.get(HEALTH_PATH)

    assert res.status_code == status.HTTP_200_OK
    data = res.json()

    assert set(data) == {"status"}
    assert data["status"] in {"ok", "degraded"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_healthz_response_structure(client: httpx.AsyncClient) -> None:
    """Healthz response should have the exact HealthStatus shape."""
    res = await client.get(HEALTHZ_PATH)

    assert res.status_code == status.HTTP_200_OK
    data = res.json()

    assert set(data) == {"status"}
    assert data["status"] in {"ok", "degraded"}
