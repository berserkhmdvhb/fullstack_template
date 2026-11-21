# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The application never migrates: startup leaves an empty database empty."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from asgi_lifespan import LifespanManager

from tests.utils.migration_scripts import db_file
from your_service_name.backend.db import engine as engine_module
from your_service_name.backend.main import create_app
from your_service_name.backend.settings import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.fixture(autouse=True)
def _migrate_integration_and_e2e() -> None:
    """Override the shared fixture: these tests start from an empty database."""


@pytest.fixture
def app() -> FastAPI:
    return create_app()


def test_init_db_has_no_migration_path() -> None:
    assert not hasattr(engine_module, "run_migrations")
    assert not hasattr(engine_module, "_run_migrations_with_retry")


@pytest.mark.integration
@pytest.mark.sqlite_only
async def test_startup_creates_no_tables(app: FastAPI) -> None:
    url = Settings().database_url
    assert url
    async with LifespanManager(app):
        pass
    with sqlite3.connect(db_file(url)) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    assert tables == []
