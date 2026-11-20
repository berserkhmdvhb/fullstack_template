# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Integration tests for Item CRUD API endpoints.

Tests all 5 endpoints (list, get, create, update, delete) against a live
SQLite database via the AsyncClient + ASGITransport. Marked with @pytest.mark.integration.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Final

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI, status

from your_service_name.backend.main import create_app

EXPECTED_TOTAL: int = 2
DEFAULT_PAGE_SIZE: int = 50
PAGINATION_TOTAL_ITEMS: int = 5
PAGINATION_PAGE_SIZE: int = 2
PAGINATION_TOTAL_PAGES: int = 3

API_BASE: Final[str] = "/api/v1"
ITEMS_PATH: Final[str] = f"{API_BASE}/items"


# Fixtures


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FastAPI:
    """Create a fresh FastAPI app with lifespan (runs DB migrations)."""
    # Use a file-based SQLite database for test isolation (avoids PostgreSQL dependency).
    # File-based ensures migrations and queries use the same database.
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client with lifespan manager for DB-backed tests."""
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_items_empty(client: httpx.AsyncClient) -> None:
    """GET /items returns empty list when no items exist."""
    res = await client.get(ITEMS_PATH)
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == DEFAULT_PAGE_SIZE
    assert data["total_pages"] == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_items_after_create(client: httpx.AsyncClient) -> None:
    """GET /items returns created items."""
    # Create an item first
    create_res = await client.post(
        ITEMS_PATH,
        json={"name": "Test Item", "description": "A test", "is_active": True},
    )
    assert create_res.status_code == status.HTTP_201_CREATED

    # List items
    res = await client.get(ITEMS_PATH)
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == 1
    assert data["page"] == 1
    assert data["page_size"] == DEFAULT_PAGE_SIZE
    assert data["total_pages"] == 1
    assert data["items"][0]["name"] == "Test Item"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_items_with_search(client: httpx.AsyncClient) -> None:
    """GET /items?search= filters by name/description."""
    await client.post(ITEMS_PATH, json={"name": "Alpha", "description": "First"})
    await client.post(ITEMS_PATH, json={"name": "Beta", "description": "Second"})
    await client.post(ITEMS_PATH, json={"name": "Gamma", "description": "Alpha-related"})

    res = await client.get(f"{ITEMS_PATH}?search=Alpha")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["total"] == EXPECTED_TOTAL
    names = {item["name"] for item in data["items"]}
    assert names == {"Alpha", "Gamma"}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_item_found(client: httpx.AsyncClient) -> None:
    """GET /items/{id} returns the item when it exists."""
    create_res = await client.post(
        ITEMS_PATH,
        json={"name": "Fetch Me", "description": "Get this one"},
    )
    item_id = create_res.json()["id"]

    res = await client.get(f"{ITEMS_PATH}/{item_id}")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["id"] == item_id
    assert data["name"] == "Fetch Me"
    assert data["description"] == "Get this one"
    assert data["is_active"] is True
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_item_not_found(client: httpx.AsyncClient) -> None:
    """GET /items/{id} returns 404 when item does not exist."""
    fake_id = str(uuid.uuid4())
    res = await client.get(f"{ITEMS_PATH}/{fake_id}")
    assert res.status_code == status.HTTP_404_NOT_FOUND
    assert "not found" in res.json()["detail"].lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_item_success(client: httpx.AsyncClient) -> None:
    """POST /items creates an item and returns 201."""
    res = await client.post(
        ITEMS_PATH,
        json={"name": "New Item", "description": "Fresh item", "is_active": True},
    )
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["name"] == "New Item"
    assert data["description"] == "Fresh item"
    assert data["is_active"] is True
    assert data["id"] is not None
    # Location header should be set
    assert "/items/" in res.headers.get("location", "")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_item_defaults(client: httpx.AsyncClient) -> None:
    """POST /items uses defaults for optional fields."""
    res = await client.post(ITEMS_PATH, json={"name": "Minimal"})
    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["description"] == ""
    assert data["is_active"] is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_item_validation_name_required(client: httpx.AsyncClient) -> None:
    """POST /items returns 422 when name is missing."""
    res = await client.post(ITEMS_PATH, json={"description": "No name"})
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_item_validation_name_max_length(client: httpx.AsyncClient) -> None:
    """POST /items returns 422 when name exceeds 255 chars."""
    res = await client.post(ITEMS_PATH, json={"name": "x" * 256})
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_item_extra_fields_forbidden(client: httpx.AsyncClient) -> None:
    """POST /items returns 422 when extra fields are provided."""
    res = await client.post(
        ITEMS_PATH,
        json={"name": "Test", "extra_field": "not allowed"},
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_item_success(client: httpx.AsyncClient) -> None:
    """PATCH /items/{id} updates specified fields."""
    create_res = await client.post(ITEMS_PATH, json={"name": "Original", "description": "Desc"})
    item_id = create_res.json()["id"]

    res = await client.patch(
        f"{ITEMS_PATH}/{item_id}",
        json={"name": "Updated", "is_active": False},
    )
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["name"] == "Updated"
    assert data["is_active"] is False
    # Description should be preserved
    assert data["description"] == "Desc"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_item_partial(client: httpx.AsyncClient) -> None:
    """PATCH /items/{id} with single field preserves others."""
    create_res = await client.post(ITEMS_PATH, json={"name": "Keep", "description": "Keep too"})
    item_id = create_res.json()["id"]

    res = await client.patch(f"{ITEMS_PATH}/{item_id}", json={"description": "Changed"})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["name"] == "Keep"  # unchanged
    assert data["description"] == "Changed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_item_not_found(client: httpx.AsyncClient) -> None:
    """PATCH /items/{id} returns 404 when item does not exist."""
    fake_id = str(uuid.uuid4())
    res = await client.patch(f"{ITEMS_PATH}/{fake_id}", json={"name": "Nope"})
    assert res.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_item_empty_payload_returns_current(
    client: httpx.AsyncClient,
) -> None:
    """PATCH /items/{id} with empty payload returns current state."""
    create_res = await client.post(ITEMS_PATH, json={"name": "Current", "description": "State"})
    item_id = create_res.json()["id"]

    res = await client.patch(f"{ITEMS_PATH}/{item_id}", json={})
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data["name"] == "Current"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_item_success(client: httpx.AsyncClient) -> None:
    """DELETE /items/{id} returns 204 and removes the item."""
    create_res = await client.post(ITEMS_PATH, json={"name": "Delete Me"})
    item_id = create_res.json()["id"]

    res = await client.delete(f"{ITEMS_PATH}/{item_id}")
    assert res.status_code == status.HTTP_204_NO_CONTENT

    # Verify gone
    get_res = await client.get(f"{ITEMS_PATH}/{item_id}")
    assert get_res.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.asyncio
async def test_delete_item_not_found(client: httpx.AsyncClient) -> None:
    """DELETE /items/{id} returns 404 when item does not exist."""
    fake_id = str(uuid.uuid4())
    res = await client.delete(f"{ITEMS_PATH}/{fake_id}")
    assert res.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_crud_workflow(client: httpx.AsyncClient) -> None:
    """
    End-to-end: create → list → get → update → delete → verify gone.
    """
    # Create
    create_res = await client.post(
        ITEMS_PATH,
        json={"name": "Workflow", "description": "Full test"},
    )
    assert create_res.status_code == status.HTTP_201_CREATED
    item_id = create_res.json()["id"]

    # List
    list_res = await client.get(ITEMS_PATH)
    assert list_res.status_code == status.HTTP_200_OK
    assert list_res.json()["total"] >= 1

    # Get
    get_res = await client.get(f"{ITEMS_PATH}/{item_id}")
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.json()["name"] == "Workflow"

    # Update
    patch_res = await client.patch(
        f"{ITEMS_PATH}/{item_id}",
        json={"name": "Workflow Updated"},
    )
    assert patch_res.status_code == status.HTTP_200_OK
    assert patch_res.json()["name"] == "Workflow Updated"

    # Delete
    del_res = await client.delete(f"{ITEMS_PATH}/{item_id}")
    assert del_res.status_code == status.HTTP_204_NO_CONTENT

    # Verify gone
    gone_res = await client.get(f"{ITEMS_PATH}/{item_id}")
    assert gone_res.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_items_pagination(client: httpx.AsyncClient) -> None:
    """GET /items?page=1&page_size=2 returns paginated results."""
    # Create PAGINATION_TOTAL_ITEMS items
    for i in range(PAGINATION_TOTAL_ITEMS):
        await client.post(ITEMS_PATH, json={"name": f"Item {i}", "description": f"Desc {i}"})

    # Get first page (PAGINATION_PAGE_SIZE items)
    res = await client.get(f"{ITEMS_PATH}?page=1&page_size={PAGINATION_PAGE_SIZE}")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert len(data["items"]) == PAGINATION_PAGE_SIZE
    assert data["total"] == PAGINATION_TOTAL_ITEMS
    assert data["page"] == 1
    assert data["page_size"] == PAGINATION_PAGE_SIZE
    assert data["total_pages"] == PAGINATION_TOTAL_PAGES

    # Get second page
    res = await client.get(f"{ITEMS_PATH}?page=2&page_size={PAGINATION_PAGE_SIZE}")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert len(data["items"]) == PAGINATION_PAGE_SIZE
    assert data["page"] == 2  # noqa: PLR2004

    # Get last page (1 item)
    url = f"{ITEMS_PATH}?page={PAGINATION_TOTAL_PAGES}&page_size={PAGINATION_PAGE_SIZE}"
    res = await client.get(url)
    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert len(data["items"]) == 1
    assert data["page"] == PAGINATION_TOTAL_PAGES
    assert data["total_pages"] == PAGINATION_TOTAL_PAGES
