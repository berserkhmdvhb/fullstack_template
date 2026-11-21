# your-service-name — adaptable service template (see wiki/getting-started.md)
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from your_service_name.backend.db.run_migrations import run_migrations
from your_service_name.backend.main import create_app
from your_service_name.backend.settings import Settings

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


def migrate_database() -> None:
    """Bring the test database (``TEST_DATABASE_URL``) to head, as the migration Job does."""
    asyncio.run(run_migrations(Settings()))


@pytest.fixture
def migrated_db() -> None:
    """The test database at head: the app no longer migrates at startup."""
    migrate_database()


@pytest.fixture(autouse=True)
def _migrate_integration_and_e2e(
    request: pytest.FixtureRequest, _set_test_database_url: None
) -> None:
    """Integration and e2e tests run against a migrated database (override to opt out)."""
    if request.node.get_closest_marker("integration") or request.node.get_closest_marker("e2e"):
        migrate_database()


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
    api_version: str = Settings().API_VERSION
    base: Final[str] = f"/api/{api_version}"
    return base
