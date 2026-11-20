# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
SAMPLE/REFERENCE: Programmatic Alembic migration runner.

Responsibilities:
- Run `alembic upgrade head` programmatically at application startup.

Public API:
- `run_migrations`: Async function that runs pending migrations.

Operational:
- Concurrency: Called once at startup from the lifespan hook.
- Logging: Logs migration execution.

Usage
-----
from your_service_name.backend.db.run_migrations import run_migrations
await run_migrations()
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from your_service_name.backend.db._constants import default_sqlite_url, project_root

logger = logging.getLogger(__name__)


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


async def run_migrations() -> None:
    """
    Run Alembic migrations programmatically.

    Notes
    -----
    - Reads `alembic.ini` from the project root.
    - Uses `alembic.command.upgrade` to apply all pending migrations.
    - Safe to call multiple times (idempotent).
    """
    if not Path(_ALEMBIC_CFG_PATH_STR).is_file():
        logger.warning("alembic.ini not found at %s; skipping migrations.", _ALEMBIC_CFG_PATH_STR)
        return

    # Run alembic in a thread to avoid blocking the event loop.
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_alembic_sync, _ALEMBIC_CFG_PATH_STR)


def _run_alembic_sync(cfg_path: str) -> None:
    """
    Synchronous Alembic runner (executed in a thread pool).

    Parameters
    ----------
    cfg_path : str
        Path to alembic.ini.

    Notes
    -----
    - Changes the working directory to the alembic.ini file's directory before
      running Alembic, ensuring that relative paths in alembic.ini (such as
      ``script_location``) resolve correctly regardless of the caller's cwd.
    """
    cfg = Config(cfg_path)

    # The migration *scripts* ship inside the installed package
    # (`your_service_name/backend/db/migrations`, next to this file), not under
    # the repo's `src/` tree — which is absent from the runtime image. alembic.ini
    # declares `script_location = src/.../migrations` (relative to the repo root),
    # so it fails to resolve in the container. Point it at the packaged migrations
    # directory absolutely, independent of the current working directory.
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parent / "migrations"))

    # Override sqlalchemy.url from environment if set (for test isolation); else
    # use the shared SQLite fallback (single source with engine.py) rather than
    # alembic.ini's static literal, so the app and migrations agree on the file.
    db_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if db_url:
        cfg.set_main_option("sqlalchemy.url", db_url)
    else:
        cfg.set_main_option("sqlalchemy.url", default_sqlite_url(project_root(Path(__file__))))

    # Ensure the parent directory exists for SQLite database files.
    _ensure_sqlite_dir(cfg, db_url)

    # Alembic resolves relative paths (e.g., script_location) from cwd,
    # so temporarily shift cwd to the alembic.ini directory.
    cfg_dir = str(Path(cfg_path).resolve().parent)
    original_cwd = Path.cwd()
    try:
        os.chdir(cfg_dir)
        command.upgrade(cfg, "head")
    finally:
        os.chdir(original_cwd)


def _ensure_sqlite_dir(cfg: Config, env_db_url: str | None) -> None:
    """
    Ensure the parent directory exists for SQLite database files.

    Parameters
    ----------
    cfg : Config
        Alembic configuration object.
    env_db_url : str or None
        Database URL from environment variables (takes precedence over config).

    Notes
    -----
    - Only applies to SQLite URLs (sqlite:// or sqlite+aiosqlite://).
    - Skips in-memory databases (:memory:).
    - Creates the parent directory if it doesn't exist.
    """
    url = env_db_url or cfg.get_main_option("sqlalchemy.url")
    if not url or not url.startswith("sqlite"):
        return
    if ":memory:" in url:
        return

    # Extract path: sqlite+aiosqlite:///./data/items.db → ./data/items.db
    path_part = url.split("///", 1)[-1] if "///" in url else url.split("://", 1)[-1]
    if not path_part:
        return

    db_path = Path(path_part).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    # Entry point for `python -m your_service_name.backend.db.run_migrations`,
    # which is what both the Kubernetes migration Job (k8s/base/migration-job.yaml)
    # and infra/db/run_migrations.sh invoke. Without this guard the module was
    # import-only: `python -m …` loaded it (emitting the rate-limiting banner and
    # a runpy warning) and exited 0 WITHOUT running Alembic, so the migration Job
    # reported "Complete" while the target Postgres schema stayed empty — and the
    # backend's lifespan `init_db` failure is swallowed (main.py), so /health
    # stayed green while every /items request 500'd on a missing table.
    asyncio.run(run_migrations())
