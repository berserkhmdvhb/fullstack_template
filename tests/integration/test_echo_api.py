# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Integration tests for Echo API endpoints.

Covers:
- POST /echo: returns 201 with Location header
- GET /echo/history: returns list
- Validation: empty text rejected, extra fields rejected
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
ECHO_PATH: Final[str] = f"{API_BASE}/echo"
EXPECTED_ECHO_COUNT: Final[int] = 2


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
async def test_echo_creates_entry(client: httpx.AsyncClient) -> None:
    """POST /echo should create an echo entry and return 201."""
    res = await client.post(ECHO_PATH, json={"text": "hello"})

    assert res.status_code == status.HTTP_201_CREATED
    data = res.json()
    assert data["text"] == "hello"
    assert data["length"] == len("hello")
    assert "Location" in res.headers


@pytest.mark.integration
@pytest.mark.asyncio
async def test_echo_empty_text_rejected(client: httpx.AsyncClient) -> None:
    """POST /echo should reject empty text."""
    res = await client.post(ECHO_PATH, json={"text": ""})

    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.integration
@pytest.mark.asyncio
async def test_echo_extra_fields_rejected(client: httpx.AsyncClient) -> None:
    """POST /echo should reject extra fields."""
    res = await client.post(ECHO_PATH, json={"text": "hello", "extra": "field"})

    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.integration
@pytest.mark.asyncio
async def test_echo_history_empty(client: httpx.AsyncClient) -> None:
    """GET /echo/history should return empty list initially."""
    res = await client.get(f"{ECHO_PATH}/history")

    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert data == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_echo_history_after_create(client: httpx.AsyncClient) -> None:
    """GET /echo/history should return created entries."""
    # Create two entries
    await client.post(ECHO_PATH, json={"text": "first"})
    await client.post(ECHO_PATH, json={"text": "second"})

    # Get history
    res = await client.get(f"{ECHO_PATH}/history")

    assert res.status_code == status.HTTP_200_OK
    data = res.json()
    assert len(data) == len(["first", "second"])
    assert data[0]["text"] == "first"
    assert data[1]["text"] == "second"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_echo_history_preserves_order(client: httpx.AsyncClient) -> None:
    """GET /echo/history should preserve insertion order."""
    texts = ["alpha", "beta", "gamma"]

    for text in texts:
        await client.post(ECHO_PATH, json={"text": text})

    res = await client.get(f"{ECHO_PATH}/history")
    data = res.json()

    assert [item["text"] for item in data] == texts
