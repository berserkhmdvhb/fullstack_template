# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Live tests for Items CRUD against running backend.

Covers:
- Full items CRUD against running backend
- Read host/port from env vars (BACKEND_HOST/BACKEND_PORT)
"""

from __future__ import annotations

import os
import uuid
from typing import Final

import httpx
import pytest
from fastapi import status


def _backend_url() -> str:
    """Compute backend base URL from environment."""
    host: Final[str] = os.getenv("BACKEND_HOST", "127.0.0.1")
    port: Final[str] = os.getenv("BACKEND_PORT", "8000")
    return f"http://{host}:{port}"


def _api_base() -> str:
    """Compute versioned API base (e.g., '/api/v1') from environment."""
    api_version: Final[str] = os.getenv("API_VERSION", "v1")
    return f"/api/{api_version}"


def _client(base_url: str, timeout: float = 10.0) -> httpx.AsyncClient:
    """Build an httpx client with ambient proxy config disabled.

    CI runners (especially self-hosted ones) frequently export
    HTTP_PROXY/HTTPS_PROXY/ALL_PROXY. httpx honours those by default and would
    route loopback (127.0.0.1) traffic through the proxy, failing every request
    to the locally-started services while the no-proxy waiter still succeeds.
    ``trust_env=False`` ignores env/netrc proxies — the httpx parallel to
    wait_for_service.py's ``_NO_PROXY_OPENER``.
    """
    return httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False)


# ---------------------------------------------------------------------------
# Tests: Items CRUD
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_items_create() -> None:
    """POST /items should create an item on running backend."""
    base_url = _backend_url()
    api_base = _api_base()

    async with _client(base_url=base_url, timeout=10.0) as client:
        res = await client.post(
            f"{api_base}/items",
            json={"name": "Live Test Item", "description": "Created by live test"},
        )

        assert res.status_code == status.HTTP_201_CREATED
        data = res.json()
        assert data["name"] == "Live Test Item"
        assert data["description"] == "Created by live test"
        assert data["is_active"] is True
        assert "id" in data


@pytest.mark.live
@pytest.mark.asyncio
async def test_items_list() -> None:
    """GET /items should return items list from running backend."""
    base_url = _backend_url()
    api_base = _api_base()

    async with _client(base_url=base_url, timeout=10.0) as client:
        res = await client.get(f"{api_base}/items")

        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data


@pytest.mark.live
@pytest.mark.asyncio
async def test_items_get() -> None:
    """GET /items/{id} should retrieve an item from running backend."""
    base_url = _backend_url()
    api_base = _api_base()

    async with _client(base_url=base_url, timeout=10.0) as client:
        # Create an item first
        create_res = await client.post(
            f"{api_base}/items",
            json={"name": "Get Test Item"},
        )
        assert create_res.status_code == status.HTTP_201_CREATED
        item_id = create_res.json()["id"]

        # Get the item
        get_res = await client.get(f"{api_base}/items/{item_id}")

        assert get_res.status_code == status.HTTP_200_OK
        data = get_res.json()
        assert data["name"] == "Get Test Item"
        assert data["id"] == item_id


@pytest.mark.live
@pytest.mark.asyncio
async def test_items_update() -> None:
    """PATCH /items/{id} should update an item on running backend."""
    base_url = _backend_url()
    api_base = _api_base()

    async with _client(base_url=base_url, timeout=10.0) as client:
        # Create an item first
        create_res = await client.post(
            f"{api_base}/items",
            json={"name": "Original Name"},
        )
        assert create_res.status_code == status.HTTP_201_CREATED
        item_id = create_res.json()["id"]

        # Update the item
        update_res = await client.patch(
            f"{api_base}/items/{item_id}",
            json={"name": "Updated Name"},
        )

        assert update_res.status_code == status.HTTP_200_OK
        data = update_res.json()
        assert data["name"] == "Updated Name"


@pytest.mark.live
@pytest.mark.asyncio
async def test_items_delete() -> None:
    """DELETE /items/{id} should delete an item on running backend."""
    base_url = _backend_url()
    api_base = _api_base()

    async with _client(base_url=base_url, timeout=10.0) as client:
        # Create an item first
        create_res = await client.post(
            f"{api_base}/items",
            json={"name": "Delete Test Item"},
        )
        assert create_res.status_code == status.HTTP_201_CREATED
        item_id = create_res.json()["id"]

        # Delete the item
        delete_res = await client.delete(f"{api_base}/items/{item_id}")

        assert delete_res.status_code == status.HTTP_204_NO_CONTENT

        # Verify deleted
        get_res = await client.get(f"{api_base}/items/{item_id}")
        assert get_res.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.live
@pytest.mark.asyncio
async def test_items_search() -> None:
    """GET /items?search= should filter items on running backend."""
    base_url = _backend_url()
    api_base = _api_base()

    async with _client(base_url=base_url, timeout=10.0) as client:
        # Create items with unique names
        unique_prefix = f"search_test_{uuid.uuid4().hex[:8]}"
        await client.post(
            f"{api_base}/items",
            json={"name": f"{unique_prefix}_alpha"},
        )
        await client.post(
            f"{api_base}/items",
            json={"name": f"{unique_prefix}_beta"},
        )

        # Search for alpha
        search_res = await client.get(f"{api_base}/items?search={unique_prefix}_alpha")

        assert search_res.status_code == status.HTTP_200_OK
        data = search_res.json()
        assert data["total"] >= 1
        assert any(item["name"] == f"{unique_prefix}_alpha" for item in data["items"])
