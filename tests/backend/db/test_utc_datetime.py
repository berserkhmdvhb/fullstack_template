# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Every timestamp is timezone-aware UTC: loaded values carry UTC, naive binds are refused."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Table, create_engine
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.schema import CreateTable

from your_service_name.backend.db.models import Base, Item, UTCDateTime

ROOT = Path(__file__).resolve().parents[3]
DATETIME_COLUMNS = ("created_at", "updated_at", "deleted_at")


def test_every_datetime_column_uses_utc_datetime() -> None:
    columns = [
        c for t in Base.metadata.tables.values() for c in t.columns if "date" in str(c.type).lower()
    ]
    assert {c.name for c in columns} >= set(DATETIME_COLUMNS)
    assert all(isinstance(c.type, UTCDateTime) for c in columns)


def test_the_postgresql_column_type_is_unchanged() -> None:
    table = Item.__table__
    assert isinstance(table, Table)
    ddl = str(CreateTable(table).compile(dialect=PGDialect()))  # type: ignore[no-untyped-call]
    assert ddl.count("TIMESTAMP WITH TIME ZONE") == len(DATETIME_COLUMNS)


def test_the_decorator_converts_aware_binds_to_utc_and_refuses_naive() -> None:
    column = UTCDateTime()
    plus_two = timezone(timedelta(hours=2))
    bound = column.process_bind_param(datetime(2025, 1, 1, 12, tzinfo=plus_two), None)  # type: ignore[arg-type]
    assert bound == datetime(2025, 1, 1, 10, tzinfo=UTC)
    assert bound is not None
    assert bound.utcoffset() == timedelta(0)
    assert column.process_bind_param(None, None) is None  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="naive"):
        column.process_bind_param(datetime(2025, 1, 1, 12), None)  # type: ignore[arg-type]  # noqa: DTZ001


def test_the_decorator_attaches_utc_to_naive_loaded_values() -> None:
    column = UTCDateTime()
    loaded = column.process_result_value(datetime(2025, 1, 1, 12), None)  # type: ignore[arg-type]  # noqa: DTZ001
    assert loaded == datetime(2025, 1, 1, 12, tzinfo=UTC)
    assert loaded is not None
    assert loaded.tzinfo == UTC
    assert column.process_result_value(None, None) is None  # type: ignore[arg-type]


async def test_a_row_written_and_read_back_on_sqlite_is_utc(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ts.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    item_id = uuid.uuid4()
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(Item(id=item_id, name="n", deleted_at=datetime.now(UTC)))
        await session.commit()
    async with AsyncSession(engine) as session:
        row = await session.get(Item, item_id)
    await engine.dispose()
    assert row is not None
    for name in DATETIME_COLUMNS:
        assert getattr(row, name).tzinfo == UTC, name


async def test_a_naive_bind_is_refused_on_write(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ts.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine) as session:
        session.add(Item(id=uuid.uuid4(), name="n", deleted_at=datetime(2025, 1, 1)))  # noqa: DTZ001
        with pytest.raises(Exception, match="naive"):
            await session.commit()
    await engine.dispose()


def test_autogenerate_sees_no_change_after_upgrade_head(tmp_path: Path) -> None:
    """The decorator keeps the column type, so no new migration is needed."""
    url = f"sqlite:///{tmp_path / 'auto.db'}"
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option(
        "script_location", str(ROOT / "src/your_service_name/backend/db/migrations")
    )
    cfg.set_main_option("sqlalchemy.url", url.replace("sqlite:", "sqlite+aiosqlite:"))
    command.upgrade(cfg, "head")
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
