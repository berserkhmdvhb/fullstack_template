# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Async Alembic migration environment.

Responsibilities:
- Configure the Alembic environment for async database operations.
- Provide `run_migrations_online()` for async migration execution.

Operational:
- Uses `async_engine_from_config` to create an async engine from alembic.ini.
- Runs migrations synchronously on the async connection via `connection.run_sync()`.

Notes
-----
- This file is loaded by Alembic, not imported directly by application code.
- Reads `sqlalchemy.url` from the [alembic] section of alembic.ini.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from your_service_name.backend.db.models import Base

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode (SQL generation only).

    Notes
    -----
    - Used by `alembic revision --autogenerate` without a live database.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    """
    Run migrations on a sync connection (called from async context).

    Parameters
    ----------
    connection : Connection
        Sync connection from async engine.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online_async() -> None:
    """
    Async wrapper for online migrations.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode (against a live database).

    Notes
    -----
    - Creates an async engine from config and runs migrations.
    - Uses `connection.run_sync()` for synchronous migration execution.
    """
    asyncio.run(_run_migrations_online_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
