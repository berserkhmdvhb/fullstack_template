# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Integration tests for database lifecycle.

Covers:
- App starts with DB, migrations run, session works
- App shuts down cleanly
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI, status

from your_service_name.backend.main import create_app

EXPECTED_STATUS_CODE: int = 200
EXPECTED_CREATED_CODE: int = 201
EXPECTED_NO_CONTENT_CODE: int = 204

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


# ---------------------------------------------------------------------------
# Tests: App startup
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_app_starts_with_db(client: httpx.AsyncClient, api_base: str) -> None:
    """App should start successfully with database initialized."""
    # If we can make a request, the app started successfully
    res = await client.get(f"{api_base}/health")
    assert res.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migrations_run_on_startup(client: httpx.AsyncClient, api_base: str) -> None:
    """Migrations should run on app startup (items table exists)."""
    # Create an item - this will fail if migrations didn't run
    res = await client.post(
        f"{api_base}/items",
        json={"name": "Test", "description": "Test item"},
    )
    assert res.status_code == status.HTTP_201_CREATED


# ---------------------------------------------------------------------------
# Tests: Session management
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_session_works_for_crud(client: httpx.AsyncClient, api_base: str) -> None:
    """Session should work for full CRUD operations."""
    # Create
    create_res = await client.post(
        f"{api_base}/items",
        json={"name": "CRUD Test", "description": "Test CRUD"},
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    item_id = create_res.json()["id"]

    # Read
    get_res = await client.get(f"{api_base}/items/{item_id}")
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.json()["name"] == "CRUD Test"

    # Update
    update_res = await client.patch(
        f"{api_base}/items/{item_id}",
        json={"name": "Updated"},
    )
    assert update_res.status_code == status.HTTP_200_OK
    assert update_res.json()["name"] == "Updated"

    # Delete
    delete_res = await client.delete(f"{api_base}/items/{item_id}")
    assert delete_res.status_code == status.HTTP_204_NO_CONTENT


# ---------------------------------------------------------------------------
# Tests: App shutdown
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lifespan_shutdown_disposes_the_db_engine(app: FastAPI) -> None:
    """Lifespan shutdown must dispose the DB engine — assert a real side effect,
    not merely that the app object exists.

    Startup (``init_db``) leaves the module-level engine initialized; the
    lifespan ``finally`` block calls ``close_db()``, which disposes the engine
    and clears the initialized flag. Asserting both sides of the transition
    makes this fail if shutdown ever degrades to a no-op.
    """

    from your_service_name.backend.db import engine  # noqa: PLC0415 — scoped import so we read the module-level state shutdown must clear

    async with LifespanManager(app):
        # Startup ran migrations and initialized the shared engine.
        assert engine._engine is not None
        assert engine._db_initialized is True

    # After the lifespan exits, shutdown disposed the engine (real side effect).
    assert engine._engine is None
    assert engine._db_initialized is False
