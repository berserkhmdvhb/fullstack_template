# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Programmatic Alembic migration runner (the per-revision migration Job).

Responsibilities:
- Wait (bounded retry) for the database, then bring it to the code's head.
- Never downgrade: a database ahead of this release is left alone, so a rollback is safe.

Public API:
- `run_migrations`: Async function that migrates the database.

States (see `db/schema.py`)
---------------------------
- at head: no-op.
- behind or empty: ``alembic upgrade head``.
- at a revision this release does not know (newer): log "database is ahead of this release;
  nothing to do" and succeed.
- more than one head in the script directory: fail.

Operational:
- Concurrency: Run once per release by the chart's migration Job (and by the local entry points);
  the application does not migrate at startup.
- Timeouts: ``lock_timeout`` from ``DB_MIGRATION_LOCK_TIMEOUT_MS``; no ``statement_timeout``.
- Logging: Attempts and states; never the database URL.

Usage
-----
python -m your_service_name.backend.db.run_migrations
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

import asyncpg
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, pool
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from your_service_name.backend.db import schema
from your_service_name.backend.db._constants import default_sqlite_url, project_root
from your_service_name.backend.db.connect import migration_connect_args
from your_service_name.backend.settings import Settings

logger = logging.getLogger(__name__)


def _redact(text: str, url: str | None) -> str:
    """Replace the database URL and its password (also percent-encoded) in ``text`` with ``***``."""
    if not url:
        return text
    secrets = {url}
    try:
        password = make_url(url).password
    except ArgumentError:
        password = None
    if password:
        secrets |= {password, quote(password, safe="")}
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "***")
    return text


def _resolve_alembic_ini_path() -> str:
    """
    Locate alembic.ini by searching common project locations.

    Returns
    -------
    str
        Absolute path to alembic.ini.

    Raises
    ------
    FileNotFoundError
        If alembic.ini cannot be found in any expected location.
    """
    candidates = [
        # Relative to this file (package layout: db/run_migrations.py → project root)
        Path(__file__).resolve().parent.parent.parent.parent.parent / "alembic.ini",
        # Working directory (common in container deployments)
        Path.cwd() / "alembic.ini",
        # Absolute container path
        Path("/app/alembic.ini"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    searched = [str(c) for c in candidates]
    msg = f"Could not find alembic.ini. Searched: {searched}"
    raise FileNotFoundError(msg)


# Pre-compute alembic.ini path at module level (avoids ASYNC240 in async context).
_ALEMBIC_CFG_PATH_STR: str = _resolve_alembic_ini_path()


# The database may still be starting when the Job's Pod does; the connection is retried with
# capped exponential backoff (worst case about 71s: 1+2+4*17), well inside the Job's own deadline.
_DB_INIT_MAX_ATTEMPTS: Final[int] = 20
_DB_INIT_BACKOFF_BASE_S: Final[float] = 1.0
_DB_INIT_BACKOFF_MAX_S: Final[float] = 4.0


def _database_url(settings: Settings) -> str:
    """The URL the runner migrates: ``settings.database_url``, else the SQLite fallback."""
    return settings.database_url or default_sqlite_url(
        project_root(Path(__file__)), settings.SQLITE_PATH
    )


def _connect_args(url: str, settings: Settings) -> dict[str, Any]:
    """Lock timeout, connect timeout and TLS for PostgreSQL; nothing for SQLite."""
    return migration_connect_args(settings) if url.startswith("postgresql") else {}


def _create_engine(url: str, settings: Settings) -> AsyncEngine:
    return create_async_engine(
        url, poolclass=pool.NullPool, connect_args=_connect_args(url, settings)
    )


def _current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


async def _read_revision(settings: Settings, url: str) -> str | None:
    """The database's current revision (``None``: no ``alembic_version`` table or row)."""
    engine = _create_engine(url, settings)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_current_revision)
    finally:
        await engine.dispose()


# Errors that mean "the database is not accepting connections yet".
_TRANSIENT_ASYNCPG: Final = (
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
)


def _is_connection_error(exc: BaseException) -> bool:
    """True for a network-level failure worth retrying; False for anything a retry cannot fix.

    Never retried: authentication failures, TLS certificate errors, a missing or unreadable CA
    file, Alembic's own errors (for example several ``alembic_version`` rows) and anything else.
    """
    if isinstance(exc, (OperationalError, InterfaceError)):
        original = exc.orig
        return original is not None and (
            _is_connection_error(original)
            or (original.__cause__ is not None and _is_connection_error(original.__cause__))
        )
    if isinstance(exc, (ssl.SSLError, FileNotFoundError, PermissionError)):
        return False
    return isinstance(exc, (OSError, TimeoutError, *_TRANSIENT_ASYNCPG))


async def _read_revision_with_retry(settings: Settings, url: str) -> str | None:
    """`_read_revision`, retrying a refused or timed-out connection a bounded number of times."""
    for attempt in range(1, _DB_INIT_MAX_ATTEMPTS + 1):
        try:
            return await _read_revision(settings, url)
        except Exception as exc:  # any connect or auth error: retry, then give up
            if attempt >= _DB_INIT_MAX_ATTEMPTS or not _is_connection_error(exc):
                raise
            delay = min(_DB_INIT_BACKOFF_BASE_S * 2 ** (attempt - 1), _DB_INIT_BACKOFF_MAX_S)
            logger.warning(
                "Database connection attempt %d/%d failed (%s: %s); retrying in %.1fs",
                attempt,
                _DB_INIT_MAX_ATTEMPTS,
                type(exc).__name__,
                _redact(str(exc), url),
                delay,
            )
            await asyncio.sleep(delay)
    raise AssertionError  # pragma: no cover - the loop returns or raises


async def run_migrations(settings: Settings) -> None:
    """
    Bring the database to this release's head; never downgrade.

    Notes
    -----
    - Reads `alembic.ini` from the project root; skips with a warning when it is absent.
    - Safe to call repeatedly: at head it does nothing.
    - Raises ``RuntimeError`` with the database URL redacted.
    """
    if not Path(_ALEMBIC_CFG_PATH_STR).is_file():
        logger.warning("alembic.ini not found at %s; skipping migrations.", _ALEMBIC_CFG_PATH_STR)
        return
    url = _database_url(settings)
    try:
        revisions = schema.load_revisions()  # more than one head fails here, before any connect
        _ensure_sqlite_dir(url)
        state = schema.classify(await _read_revision_with_retry(settings, url), revisions)
        if state is schema.SchemaState.CURRENT:
            logger.info("Database is already at head (%s); nothing to migrate.", revisions.head)
        elif state is schema.SchemaState.AHEAD:
            logger.info("database is ahead of this release; nothing to do")
        else:
            logger.info("Database is %s; upgrading to head (%s).", state.value, revisions.head)
            # Run alembic in a thread to avoid blocking the event loop.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _run_alembic_sync, _ALEMBIC_CFG_PATH_STR, settings)
            logger.info("Migrations applied.")
    except Exception as exc:  # noqa: BLE001 - redact whatever failed
        # Connection and config errors can echo the URL; raise one that cannot.
        msg = f"{type(exc).__name__}: {_redact(str(exc), settings.database_url)}"
        raise RuntimeError(msg) from None


def _run_alembic_sync(cfg_path: str, settings: Settings) -> None:
    """
    Synchronous ``alembic upgrade head`` (executed in a thread pool).

    Notes
    -----
    - Points ``script_location`` at the packaged migrations, absolutely: the repo's ``src/`` tree is
      absent from the runtime image.
    - Changes the working directory to the alembic.ini file's directory while Alembic runs.
    """
    cfg = Config(cfg_path)
    cfg.set_main_option("script_location", str(schema.MIGRATIONS_DIR))
    url = _database_url(settings)
    # configparser interpolates `%`; escape it so a password containing one still works.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.attributes["connect_args"] = _connect_args(url, settings)
    _ensure_sqlite_dir(url)

    # Alembic resolves relative paths from cwd: shift cwd to the alembic.ini directory meanwhile.
    cfg_dir = str(Path(cfg_path).resolve().parent)
    original_cwd = Path.cwd()
    try:
        os.chdir(cfg_dir)
        command.upgrade(cfg, "head")
    finally:
        os.chdir(original_cwd)


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory of a SQLite database file (not for ``:memory:``)."""
    if not url.startswith("sqlite") or ":memory:" in url:
        return
    # sqlite+aiosqlite:///./data/items.db -> ./data/items.db
    path_part = url.split("///", 1)[-1] if "///" in url else url.split("://", 1)[-1]
    if not path_part:
        return
    Path(path_part).resolve().parent.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    # `python -m your_service_name.backend.db.run_migrations`: the migration Job and the compose
    # `migrate` service. Without this guard the module would import and exit 0 without
    # migrating anything.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(run_migrations(Settings.for_migration()))
