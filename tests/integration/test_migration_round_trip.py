# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Migrations round trip: upgrade to head, downgrade to base, upgrade to head again.

Runs on whichever database the fixtures selected (SQLite, or PostgreSQL when
``TEST_DATABASE_URL`` is a PostgreSQL URL). The shared fixtures leave the database at head.
"""

from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import migrate_database
from your_service_name.backend.db import schema
from your_service_name.backend.db.run_migrations import _ALEMBIC_CFG_PATH_STR
from your_service_name.backend.settings import Settings

pytestmark = pytest.mark.integration

HEAD = "0001"


def _alembic_config() -> Config:
    url = Settings().database_url
    assert url
    cfg = Config(_ALEMBIC_CFG_PATH_STR)
    cfg.set_main_option("script_location", str(schema.MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


async def _state() -> tuple[set[str], list[str]]:
    """The table names, and the rows of ``alembic_version`` (empty when there is no such table)."""
    url = Settings().database_url
    assert url
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as conn:
            tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
            versions: list[str] = []
            if "alembic_version" in tables:
                rows = await conn.execute(text("SELECT version_num FROM alembic_version"))
                versions = [row[0] for row in rows]
    finally:
        await engine.dispose()
    return tables, versions


def test_head_matches_the_expected_revision() -> None:
    assert schema.load_revisions().head == HEAD


def test_upgrade_downgrade_upgrade() -> None:
    cfg = _alembic_config()

    tables, versions = asyncio.run(_state())
    assert {"items", "alembic_version"} <= tables
    assert versions == [HEAD]

    command.downgrade(cfg, "base")
    tables, versions = asyncio.run(_state())
    assert "items" not in tables
    assert versions == []  # the version table stays, with no row

    command.upgrade(cfg, "head")
    tables, versions = asyncio.run(_state())
    assert {"items", "alembic_version"} <= tables
    assert versions == [HEAD]

    migrate_database()  # the runner agrees: already at head, nothing to do
    assert asyncio.run(_state())[1] == [HEAD]


@pytest.mark.postgres_only
def test_postgres_column_types() -> None:
    """On PostgreSQL the items table uses native uuid and timestamptz columns."""

    async def types() -> dict[str, str]:
        url = Settings().database_url
        assert url
        engine = create_async_engine(url, poolclass=pool.NullPool)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_name = 'items'"
                    )
                )
                return dict(rows.all())
        finally:
            await engine.dispose()

    kinds = asyncio.run(types())
    assert kinds["id"] == "uuid"
    assert kinds["created_at"] == "timestamp with time zone"
