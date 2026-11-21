# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Async engine factory, session dependency, and lifespan hooks.

Responsibilities:
- Create the async SQLAlchemy engine (PostgreSQL or SQLite fallback).
- Provide a per-request AsyncSession via FastAPI Depends.
- Create the engine at startup (it never migrates); dispose it at shutdown.

Public API:
- `DbSession`: Annotated[AsyncSession, Depends(get_session)] for endpoint injection.
- `get_session`: Async generator yielding a session per request.
- `init_db`: Lifespan startup — creates the engine.
- `close_db`: Lifespan shutdown — disposes engine.

Operational:
- Concurrency: One AsyncSession per request; engine is process-shared.
- Logging: Logs engine URL and disposal.

Usage
-----
from your_service_name.backend.db.engine import DbSession, init_db, close_db
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from your_service_name.backend.db._constants import default_sqlite_path, project_root
from your_service_name.backend.db.connect import app_connect_args

if TYPE_CHECKING:
    from fastapi import FastAPI

    from your_service_name.backend.settings import Settings

logger = logging.getLogger(__name__)


def _resolve_database_url(settings: Settings) -> str:
    """
    Resolve the database URL from the settings or fall back to SQLite.

    Returns
    -------
    str
        Async-compatible database URL.

    Notes
    -----
    - `TEST_DATABASE_URL` wins (isolated test databases), then `DATABASE_URL`
      (production/development) — see ``Settings.database_url``.
    - Falls back to an absolute SQLite path relative to the project root when unset.
    """
    url = settings.database_url
    if url:
        return url

    # Use an absolute path to ensure migrations and the engine use the same DB file
    # regardless of the working directory (migrations run with cwd=alembic.ini dir).
    # Single source with run_migrations.py / seed.py (C2); SQLITE_PATH overrides.
    root = project_root(Path(__file__))
    db_path = default_sqlite_path(root, settings.SQLITE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("DATABASE_URL not set; using SQLite fallback at %s", db_path)
    return f"sqlite+aiosqlite:///{db_path}"


# Module-level engine and session factory (lazy-initialized).
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_db_initialized: bool = False


def _engine_kwargs(url: str, settings: Settings) -> dict[str, Any]:
    """
    Build ``create_async_engine`` kwargs: always ping the pool before use; for the real
    (PostgreSQL) tier also size the pool and set the timeouts and TLS (``db/connect.py``). The
    SQLite fallback keeps SQLAlchemy's default pool and takes no connect arguments.
    """
    kwargs: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs["pool_size"] = settings.DB_POOL_SIZE
        kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
        kwargs["connect_args"] = app_connect_args(settings)
    return kwargs


def _get_engine(settings: Settings) -> AsyncEngine:
    """
    Lazily initialize and return the async engine.

    Returns
    -------
    AsyncEngine
        The SQLAlchemy async engine.
    """
    # ruff: noqa: PLW0603 — Lazy initialization requires module-level state.
    global _engine, _session_factory
    if _engine is None:
        url = _resolve_database_url(settings)
        _engine = create_async_engine(url, **_engine_kwargs(url, settings))
        _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an AsyncSession per request with auto-commit/rollback.

    Yields
    ------
    AsyncSession
        A new session for the current request.

    Notes
    -----
    - Commits on success, rolls back on exception.
    - Always closes the session in the finally block.
    - Raises RuntimeError if the session factory is unavailable (e.g., after close_db).
    """
    factory = _session_factory
    if factory is None:
        msg = (
            "Session factory is not initialized. "
            "This indicates the database engine was disposed (e.g., after close_db). "
            "Ensure init_db() has been called before making requests."
        )
        raise RuntimeError(msg)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


DbSession = Annotated[AsyncSession, Depends(get_session)]


async def init_db(app: FastAPI) -> None:
    """
    Lifespan startup hook: create the engine and session factory. It does not migrate.

    Parameters
    ----------
    app : FastAPI
        The application; its ``app.state.settings`` configures the engine.

    Notes
    -----
    - Migrations run before the app starts: the chart's per-revision migration Job, or
      ``python -m your_service_name.backend.db.run_migrations`` locally. The readiness probe
      (/health) answers 503 until the schema matches this release.
    - No connection is made here; an unreachable database shows up as a 503 readiness.
    """
    global _db_initialized
    settings: Settings = app.state.settings
    _get_engine(settings)
    _db_initialized = True
    logger.info("Database engine ready.")


async def close_db(_app: FastAPI) -> None:
    """
    Lifespan shutdown hook: dispose the async engine.

    Parameters
    ----------
    app : FastAPI
        The FastAPI application instance.
    """
    logger.info("Disposing database engine...")
    # ruff: noqa: PLW0603 — Lazy initialization requires module-level state.
    global _engine, _session_factory, _db_initialized
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        _db_initialized = False
