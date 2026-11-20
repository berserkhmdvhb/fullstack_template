# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Async engine factory, session dependency, and lifespan hooks.

Responsibilities:
- Create the async SQLAlchemy engine (PostgreSQL or SQLite fallback).
- Provide a per-request AsyncSession via FastAPI Depends.
- Run Alembic migrations at startup; dispose engine at shutdown.

Public API:
- `DbSession`: Annotated[AsyncSession, Depends(get_session)] for endpoint injection.
- `get_session`: Async generator yielding a session per request.
- `init_db`: Lifespan startup — runs migrations.
- `close_db`: Lifespan shutdown — disposes engine.

Operational:
- Concurrency: One AsyncSession per request; engine is process-shared.
- Logging: Logs engine URL and disposal.

Usage
-----
from your_service_name.backend.db.engine import DbSession, init_db, close_db
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final

from fastapi import Depends
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from your_service_name.backend.db._constants import default_sqlite_path, project_root
from your_service_name.backend.db.run_migrations import run_migrations

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)


def _resolve_database_url() -> str:
    """
    Resolve the database URL from environment or fall back to SQLite.

    Returns
    -------
    str
        Async-compatible database URL.

    Notes
    -----
    - Reads `TEST_DATABASE_URL` env var first (for isolated test databases).
    - Reads `DATABASE_URL` env var (for production/development).
    - Falls back to an absolute SQLite path relative to the project root when unset.
    """
    # Test isolation: use a unique in-memory database per process.
    test_url = os.getenv("TEST_DATABASE_URL")
    if test_url:
        return test_url

    url = os.getenv("DATABASE_URL")
    if url:
        return url

    # Use an absolute path to ensure migrations and the engine use the same DB file
    # regardless of the working directory (migrations run with cwd=alembic.ini dir).
    # Single source with run_migrations.py / seed.py (C2); SQLITE_PATH overrides.
    root = project_root(Path(__file__))
    db_path = default_sqlite_path(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("DATABASE_URL not set; using SQLite fallback at %s", db_path)
    return f"sqlite+aiosqlite:///{db_path}"


# Module-level engine and session factory (lazy-initialized).
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_db_initialized: bool = False


def _int_env(name: str, default: int) -> int:
    """Read a positive int env var, falling back to ``default`` on unset/garbage."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid int env var %s=%r; using %d", name, raw, default)
        return default
    return value if value > 0 else default


def _engine_kwargs(url: str) -> dict[str, Any]:
    """
    Build ``create_async_engine`` kwargs: always ping the pool before use, and size the
    pool from env for the real (PostgreSQL) tier. The SQLite fallback keeps SQLAlchemy's
    default pool — connection-pool tuning is a production-database concern, not a
    file-database one.
    """
    kwargs: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs["pool_size"] = _int_env("DB_POOL_SIZE", 10)
        kwargs["max_overflow"] = _int_env("DB_MAX_OVERFLOW", 20)
    return kwargs


def _get_engine() -> AsyncEngine:
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
        url = _resolve_database_url()
        _engine = create_async_engine(url, **_engine_kwargs(url))
        _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Lazily initialize and return the session factory.

    Returns
    -------
    async_sessionmaker[AsyncSession]
        The SQLAlchemy async session factory.
    """
    _get_engine()  # Ensure engine is initialized.
    if _session_factory is None:
        msg = (
            "Session factory not initialized after _get_engine(). "
            "This indicates a lazy initialization failure."
        )
        raise RuntimeError(msg)
    return _session_factory


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


# Database initialization is retried with capped exponential backoff so a
# transient "Postgres not accepting connections yet" race at pod start does not
# permanently degrade the pod. The worst-case total wait (~71s: 1+2+4*17) is
# sized to fit inside the backend startupProbe budget (30 * 5s = 150s); if the
# database is still unreachable after that, the startupProbe recycles the pod.
_DB_INIT_MAX_ATTEMPTS: Final[int] = 20
_DB_INIT_BACKOFF_BASE_S: Final[float] = 1.0
_DB_INIT_BACKOFF_MAX_S: Final[float] = 4.0


async def _run_migrations_with_retry() -> None:
    """
    Run Alembic migrations, retrying transient database-connection failures.

    Raises
    ------
    Exception
        The last error encountered if migrations do not succeed within
        ``_DB_INIT_MAX_ATTEMPTS`` attempts.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _DB_INIT_MAX_ATTEMPTS + 1):
        try:
            await run_migrations()
        except Exception as exc:  # noqa: BLE001 — retry any DB connectivity/auth error.
            last_exc = exc
            if attempt >= _DB_INIT_MAX_ATTEMPTS:
                break
            delay = min(
                _DB_INIT_BACKOFF_BASE_S * 2 ** (attempt - 1),
                _DB_INIT_BACKOFF_MAX_S,
            )
            logger.warning(
                "Migration attempt %d/%d failed: %s; retrying in %.1fs",
                attempt,
                _DB_INIT_MAX_ATTEMPTS,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
        else:
            return  # Success — migrations applied.
    if last_exc is not None:
        raise last_exc


async def init_db(_app: FastAPI) -> None:
    """
    Lifespan startup hook: run Alembic migrations (with retry) and initialize the engine.

    Parameters
    ----------
    _app : FastAPI
        The FastAPI application instance.

    Raises
    ------
    Exception
        If migrations cannot be applied within the retry budget, so the caller
        can decide how to degrade. The lifespan keeps the process up and the
        readiness probe (/health → 503) keeps the pod out of rotation until the
        database is reachable — it must never fail silent behind a green health.

    Notes
    -----
    - Runs `alembic upgrade head` programmatically, then lazily creates the
      module-level engine/session factory.
    - Sets ``_db_initialized`` only on success; the readiness probe keys off it.
    - In production migrations normally run in the migration Job / init container
      before Gunicorn starts; this is the belt-and-suspenders in-process path
      (also used by tests via LifespanManager).
    """
    global _db_initialized
    logger.info(
        "Running database migrations (up to %d attempts)...",
        _DB_INIT_MAX_ATTEMPTS,
    )
    await _run_migrations_with_retry()
    # Initialize the module-level engine and session factory for request handling.
    _get_engine()
    _db_initialized = True
    logger.info("Database migrations complete; engine ready.")


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
