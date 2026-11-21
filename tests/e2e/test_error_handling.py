# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
E2E tests for error handling.

Covers:
- 404 for non-existent items
- 422 for invalid payloads
- Error response structure
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Final

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI, status

from your_service_name.backend.main import create_app

API_BASE: Final[str] = "/api/v1"
ITEMS_PATH: Final[str] = f"{API_BASE}/items"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def app() -> FastAPI:
    """Create a fresh FastAPI app with lifespan."""
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client with lifespan manager."""
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


# ---------------------------------------------------------------------------
# Tests: 404 Not Found
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_get_nonexistent_item_returns_404(client: httpx.AsyncClient) -> None:
    """GET /items/{id} should return 404 for non-existent item."""
    fake_id = str(uuid.uuid4())

    res = await client.get(f"{ITEMS_PATH}/{fake_id}")

    assert res.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_update_nonexistent_item_returns_404(client: httpx.AsyncClient) -> None:
    """PATCH /items/{id} should return 404 for non-existent item."""
    fake_id = str(uuid.uuid4())

    res = await client.patch(
        f"{ITEMS_PATH}/{fake_id}",
        json={"name": "Updated"},
    )

    assert res.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_delete_nonexistent_item_returns_404(client: httpx.AsyncClient) -> None:
    """DELETE /items/{id} should return 404 for non-existent item."""
    fake_id = str(uuid.uuid4())

    res = await client.delete(f"{ITEMS_PATH}/{fake_id}")

    assert res.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# Tests: 422 Validation Error
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_create_item_without_name_returns_422(client: httpx.AsyncClient) -> None:
    """POST /items should return 422 when name is missing."""
    res = await client.post(ITEMS_PATH, json={"description": "No name"})

    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_create_item_with_empty_name_returns_422(client: httpx.AsyncClient) -> None:
    """POST /items should return 422 when name is empty."""
    res = await client.post(ITEMS_PATH, json={"name": "", "description": "Empty name"})

    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_create_item_with_extra_fields_returns_422(client: httpx.AsyncClient) -> None:
    """POST /items should return 422 when extra fields are present."""
    res = await client.post(
        ITEMS_PATH,
        json={"name": "Test", "description": "Test", "extra_field": "value"},
    )

    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ---------------------------------------------------------------------------
# Tests: Error response structure
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_404_response_has_detail(client: httpx.AsyncClient) -> None:
    """404 response should have detail field."""
    fake_id = str(uuid.uuid4())

    res = await client.get(f"{ITEMS_PATH}/{fake_id}")

    assert res.status_code == status.HTTP_404_NOT_FOUND
    data = res.json()
    assert "detail" in data


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_422_response_has_detail(client: httpx.AsyncClient) -> None:
    """422 response should have detail field."""
    res = await client.post(ITEMS_PATH, json={"name": ""})

    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    data = res.json()
    assert "detail" in data
