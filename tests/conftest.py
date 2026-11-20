# your-service-name — adaptable service template (see wiki/getting-started.md)
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from your_service_name.backend.main import create_app
from your_service_name.utils.versioning_api import get_api_version

TEST_BASE_URL: Final[str] = "http://test"


@pytest.fixture(autouse=True)
def _set_test_database_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    Ensure each test gets a unique file-based SQLite database.

    Using a file-based database (rather than :memory:) ensures that alembic
    migrations and application queries share the same database file.
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")


@pytest.fixture
def app() -> FastAPI:
    """Create a new FastAPI app for each test session."""
    return create_app()


@pytest_asyncio.fixture()
async def async_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """
    Async HTTP client that runs app lifespan using asgi-lifespan.
    """
    async with LifespanManager(app):  # triggers startup/shutdown
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=TEST_BASE_URL) as client:
            yield client


@pytest.fixture(scope="session")
def api_base() -> str:
    """
    Base path for versioned API routes (e.g., "/api/v1"), resolved centrally.
    """
    api_version: str = get_api_version()
    base: Final[str] = f"/api/{api_version}"
    return base
