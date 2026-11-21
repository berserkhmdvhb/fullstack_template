# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Seed script for initial database data.

Usage:
    python -m your_service_name.scripts.seed
    python -m your_service_name.scripts.seed --reset  # clear and re-seed
    python -m your_service_name.scripts.seed --database-url sqlite+aiosqlite:///./data/test.db

Works with both SQLite and PostgreSQL (database-agnostic via SQLAlchemy).

Notes:
    This script creates tables directly (without Alembic migrations) for simplicity.
    For production use with migrations, run migrations first, then seed.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import Final

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from your_service_name.backend.db._constants import default_sqlite_path, project_root
from your_service_name.backend.db.models import Base, Item
from your_service_name.backend.settings import Settings

logger = logging.getLogger(__name__)

# Seed data for initial population
SEED_DATA: Final[list[dict[str, str | bool]]] = [
    {
        "name": "Sample Item 1",
        "description": "First sample item for testing",
        "is_active": True,
    },
    {
        "name": "Sample Item 2",
        "description": "Second sample item for testing",
        "is_active": True,
    },
    {
        "name": "Sample Item 3",
        "description": "Third sample item (inactive)",
        "is_active": False,
    },
]


def _resolve_database_url(explicit_url: str | None = None) -> str:
    """
    Resolve database URL from argument, environment, or fallback.

    Parameters
    ----------
    explicit_url : str or None
        Explicit database URL from command line argument.

    Returns
    -------
    str
        Database URL to use.
    """
    if explicit_url:
        return explicit_url

    settings = Settings()
    for secret in (settings.DATABASE_URL, settings.TEST_DATABASE_URL):
        if secret is not None:
            return secret.get_secret_value()

    # Fallback to SQLite — single source with engine.py / run_migrations.py (C2).
    root = project_root(Path(__file__))
    db_path = default_sqlite_path(root, settings.SQLITE_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


async def seed_database(*, reset: bool = False, database_url: str | None = None) -> None:
    """
    Seed the database with initial data.

    Parameters
    ----------
    reset : bool
        If True, clear existing data before seeding.
    database_url : str or None
        Database URL to use (overrides environment variables).
    """
    db_url = _resolve_database_url(database_url)
    logger.info("Using database: %s", make_url(db_url).render_as_string(hide_password=True))

    # Create engine
    engine = create_async_engine(db_url, echo=False)

    try:
        # Create tables (simpler than running migrations for seed script)
        logger.info("Creating tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Tables created.")

        # Seed data
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with session_factory() as session:
            if reset:
                logger.info("Clearing existing items...")
                await session.execute(text("DELETE FROM items"))
                await session.commit()
                logger.info("Existing items cleared.")

            # Seed new items
            for data in SEED_DATA:
                item = Item(**data)
                session.add(item)

            await session.commit()
            logger.info("Seeded %d items successfully.", len(SEED_DATA))

    finally:
        await engine.dispose()
        logger.info("Engine disposed.")


def main() -> None:
    """Entry point for seed script."""
    parser = argparse.ArgumentParser(description="Seed database with initial data")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing data before seeding",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="Database URL (overrides DATABASE_URL env var)",
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    logger.info("Starting seed script...")

    # Run seed
    asyncio.run(seed_database(reset=args.reset, database_url=args.database_url))

    logger.info("Seed script complete.")


if __name__ == "__main__":
    main()
