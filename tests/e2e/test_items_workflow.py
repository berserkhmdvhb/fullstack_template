# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
E2E tests for Items CRUD workflow.

Covers:
- Full CRUD workflow: create → list → get → update → delete
- Search across the workflow
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Final

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI, status

from your_service_name.backend.main import create_app

API_BASE: Final[str] = "/api/v1"
ITEMS_PATH: Final[str] = f"{API_BASE}/items"
EXPECTED_ITEMS_COUNT: Final[int] = 3


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
# Tests: Full CRUD workflow
# ---------------------------------------------------------------------------


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_full_crud_workflow(client: httpx.AsyncClient) -> None:
    """Test complete CRUD workflow: create → read → update → delete."""
    # CREATE
    create_res = await client.post(
        ITEMS_PATH,
        json={"name": "Workflow Test", "description": "Testing full workflow"},
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    created_item = create_res.json()
    item_id = created_item["id"]

    # READ
    get_res = await client.get(f"{ITEMS_PATH}/{item_id}")
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.json()["name"] == "Workflow Test"

    # UPDATE
    update_res = await client.patch(
        f"{ITEMS_PATH}/{item_id}",
        json={"name": "Updated Workflow Test"},
    )
    assert update_res.status_code == status.HTTP_200_OK
    assert update_res.json()["name"] == "Updated Workflow Test"

    # DELETE
    delete_res = await client.delete(f"{ITEMS_PATH}/{item_id}")
    assert delete_res.status_code == status.HTTP_204_NO_CONTENT

    # VERIFY DELETED
    get_res_after = await client.get(f"{ITEMS_PATH}/{item_id}")
    assert get_res_after.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_create_multiple_and_list(client: httpx.AsyncClient) -> None:
    """Test creating multiple items and listing them."""
    # Create 3 items
    for i in range(3):
        res = await client.post(
            ITEMS_PATH,
            json={"name": f"Item {i}", "description": f"Description {i}"},
        )
        assert res.status_code == status.HTTP_201_CREATED

    # List all
    list_res = await client.get(ITEMS_PATH)
    assert list_res.status_code == status.HTTP_200_OK
    data = list_res.json()
    assert data["total"] == EXPECTED_ITEMS_COUNT
    assert len(data["items"]) == EXPECTED_ITEMS_COUNT


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_search_across_workflow(client: httpx.AsyncClient) -> None:
    """Test search functionality across CRUD operations."""
    # Create items with different names
    await client.post(ITEMS_PATH, json={"name": "Alpha Item", "description": "First"})
    await client.post(ITEMS_PATH, json={"name": "Beta Item", "description": "Second"})
    await client.post(ITEMS_PATH, json={"name": "Gamma Item", "description": "Third"})

    # Search for "alpha"
    search_res = await client.get(f"{ITEMS_PATH}?search=alpha")
    assert search_res.status_code == status.HTTP_200_OK
    data = search_res.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Alpha Item"

    # Search for "item" (should match all)
    search_res_all = await client.get(f"{ITEMS_PATH}?search=item")
    assert search_res_all.status_code == status.HTTP_200_OK
    data_all = search_res_all.json()
    assert data_all["total"] == EXPECTED_ITEMS_COUNT


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_partial_update_workflow(client: httpx.AsyncClient) -> None:
    """Test partial update (only changing some fields)."""
    # Create
    create_res = await client.post(
        ITEMS_PATH,
        json={"name": "Original", "description": "Original desc", "is_active": True},
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    item_id = create_res.json()["id"]

    # Partial update (only name)
    update_res = await client.patch(
        f"{ITEMS_PATH}/{item_id}",
        json={"name": "Updated Name"},
    )
    assert update_res.status_code == status.HTTP_200_OK
    updated = update_res.json()

    # Verify only name changed
    assert updated["name"] == "Updated Name"
    assert updated["description"] == "Original desc"  # unchanged
    assert updated["is_active"] is True  # unchanged
