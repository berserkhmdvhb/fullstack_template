# your-service-name — adaptable service template (see wiki/getting-started.md)
import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Final

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from your_service_name.backend.db.models import Base
from your_service_name.backend.db.run_migrations import run_migrations
from your_service_name.backend.main import create_app
from your_service_name.backend.settings import Settings

TEST_BASE_URL: Final[str] = "http://test"


# A PostgreSQL URL in the caller's environment selects PostgreSQL for the integration, e2e and
# contract layers (CI's service container, or a local server: see wiki/testing.md). Unit tests
# always use SQLite.
_EXTERNAL_TEST_DATABASE_URL: Final[str] = os.environ.get("TEST_DATABASE_URL", "")
POSTGRES_TEST_URL: Final[str | None] = (
    _EXTERNAL_TEST_DATABASE_URL if _EXTERNAL_TEST_DATABASE_URL.startswith("postgresql") else None
)
_DB_LAYERS: Final[tuple[str, ...]] = ("integration", "e2e", "contract")


def _uses_postgres(request: pytest.FixtureRequest) -> bool:
    return POSTGRES_TEST_URL is not None and any(
        request.node.get_closest_marker(layer) for layer in _DB_LAYERS
    )


def _truncate_all_tables(url: str) -> None:
    """Empty every application table (the schema and ``alembic_version`` stay)."""

    async def run() -> None:
        engine = create_async_engine(url, poolclass=pool.NullPool)
        try:
            async with engine.begin() as conn:
                names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
                await conn.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.fixture(autouse=True)
def _set_test_database_url(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    """
    Select the test database for each test.

    PostgreSQL layers share one database that was migrated once (the schema is made by the
    migrations, never ``create_all``) and are emptied by a truncate after each test. Everything
    else gets a unique file-based SQLite database, so migrations and application queries share
    one file (``:memory:`` would not).
    """
    postgres = _uses_postgres(request)
    if request.node.get_closest_marker("postgres_only") and not postgres:
        pytest.skip("needs PostgreSQL: set TEST_DATABASE_URL to a postgresql+asyncpg:// URL")
    if request.node.get_closest_marker("sqlite_only") and postgres:
        pytest.skip("manipulates a SQLite database file directly")
    if postgres:
        assert POSTGRES_TEST_URL is not None
        monkeypatch.setenv("TEST_DATABASE_URL", POSTGRES_TEST_URL)
        migrate_database()
        yield
        _truncate_all_tables(POSTGRES_TEST_URL)
    else:
        monkeypatch.setenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        yield


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
