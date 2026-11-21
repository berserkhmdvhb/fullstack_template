# your-service-name — adaptable service template (see wiki/getting-started.md)
"""A database password must neither break the Alembic config nor reach a log or an error."""

from __future__ import annotations

import asyncio
import logging
import traceback
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from pydantic import SecretStr

from your_service_name.backend.db import engine as engine_module
from your_service_name.backend.db import run_migrations as migrations
from your_service_name.backend.settings import Settings

if TYPE_CHECKING:
    from alembic.config import Config

ROOT = Path(__file__).resolve().parents[3]
MARKER = "MARKERPW"
# A `%` makes configparser interpolate; the marker lets us spot any leak. Obviously fake.
PASSWORD = f"p%zz-{MARKER}"
URL = f"postgresql+asyncpg://app:{PASSWORD}@127.0.0.1:1/db"


@pytest.fixture
def settings() -> Settings:
    return Settings(DATABASE_URL=SecretStr(URL), TEST_DATABASE_URL=None)


def test_a_percent_in_the_password_configures_alembic(settings: Settings) -> None:
    seen: list[str | None] = []

    def fake_upgrade(cfg: Config, _revision: str) -> None:
        seen.append(cfg.get_main_option("sqlalchemy.url"))

    with patch.object(command, "upgrade", fake_upgrade):
        migrations._run_alembic_sync(str(ROOT / "alembic.ini"), settings)
    assert seen == [URL]


async def test_a_failed_migration_error_does_not_echo_the_url(settings: Settings) -> None:
    boom = RuntimeError(f"could not connect to {URL} (password {PASSWORD})")
    with (
        patch.object(migrations, "_run_alembic_sync", side_effect=boom),
        pytest.raises(RuntimeError) as excinfo,
    ):
        await migrations.run_migrations(settings)
    text = "".join(traceback.format_exception(excinfo.value))
    assert MARKER not in text
    assert "RuntimeError" in text  # the original type is still named


async def test_the_retry_loop_logs_no_password(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    boom = RuntimeError(f"could not connect to {URL} (password {PASSWORD})")
    logging.getLogger(engine_module.__name__).disabled = False
    with (
        patch.object(migrations, "_run_alembic_sync", side_effect=boom),
        patch.object(engine_module, "_DB_INIT_MAX_ATTEMPTS", 2),
        patch.object(asyncio, "sleep", new=AsyncMock()),
        caplog.at_level(logging.DEBUG),
        pytest.raises(RuntimeError) as excinfo,
    ):
        await engine_module._run_migrations_with_retry(settings)
    assert "failed" in caplog.text  # the warning was logged ...
    assert MARKER not in caplog.text  # ... without the password
    assert MARKER not in "".join(traceback.format_exception(excinfo.value))
