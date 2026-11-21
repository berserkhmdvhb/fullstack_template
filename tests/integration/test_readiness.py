# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Readiness checks, read-only, that the database schema matches the release; liveness never does.

Real Alembic revisions on SQLite: the script directory is a throwaway one, so a database can be
at head, behind, empty or ahead of "this release".
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from tests.utils.migration_scripts import make_script_dir, revision_of, stamp
from your_service_name.backend.db import run_migrations as runner
from your_service_name.backend.db import schema
from your_service_name.backend.main import create_app
from your_service_name.backend.settings import Settings

HEALTH = "/api/v1/health"
HEALTHZ = "/api/v1/healthz"
OK = 200
UNAVAILABLE = 503
TWO = [("0001", None), ("0002", "0001")]

pytestmark = [pytest.mark.integration, pytest.mark.sqlite_only]


@pytest.fixture(autouse=True)
def _migrate_integration_and_e2e() -> None:
    """Override the shared fixture: each test sets up its own database state."""


def _release(
    monkeypatch: pytest.MonkeyPatch, root: Path, revisions: list[tuple[str, str | None]]
) -> None:
    """Make the code under test expect ``revisions``."""
    monkeypatch.setattr(schema, "MIGRATIONS_DIR", make_script_dir(root, revisions))


@asynccontextmanager
async def _serve() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_ready_when_the_schema_is_at_head(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path, TWO)
    await runner.run_migrations(Settings())
    async with _serve() as client:
        res = await client.get(HEALTH)
    assert res.status_code == OK
    assert res.json() == {"status": "ok"}


async def test_ready_when_the_database_is_ahead_of_this_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path, TWO)
    await runner.run_migrations(Settings())
    stamp(Settings().database_url or "", "0003_from_a_newer_release")
    async with _serve() as client:
        res = await client.get(HEALTH)
    assert res.status_code == OK
    assert res.json() == {"status": "ok"}


async def test_not_ready_when_the_schema_is_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path / "old", [("0001", None)])
    await runner.run_migrations(Settings())
    _release(monkeypatch, tmp_path / "new", TWO)
    async with _serve() as client:
        res = await client.get(HEALTH)
    assert res.status_code == UNAVAILABLE
    assert res.json() == {"status": "degraded", "reason": "schema_behind"}


async def test_not_ready_when_the_version_table_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path, TWO)
    async with _serve() as client:
        res = await client.get(HEALTH)
    assert res.status_code == UNAVAILABLE
    assert res.json() == {"status": "degraded", "reason": "schema_missing"}


async def test_not_ready_when_the_version_table_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path, TWO)
    stamp(Settings().database_url or "", None)
    async with _serve() as client:
        res = await client.get(HEALTH)
    assert res.status_code == UNAVAILABLE
    assert res.json() == {"status": "degraded", "reason": "schema_missing"}


async def test_not_ready_when_the_database_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path, TWO)
    monkeypatch.setenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/no-such-dir/x.db")
    async with _serve() as client:
        res = await client.get(HEALTH)
    assert res.status_code == UNAVAILABLE
    assert res.json() == {"status": "degraded", "reason": "database_unreachable"}


async def test_liveness_passes_while_readiness_fails_on_a_behind_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path / "old", [("0001", None)])
    await runner.run_migrations(Settings())
    _release(monkeypatch, tmp_path / "new", TWO)
    async with _serve() as client:
        ready = await client.get(HEALTH)
        live = await client.get(HEALTHZ)
    assert ready.status_code == UNAVAILABLE
    assert live.status_code == OK
    assert live.json() == {"status": "ok"}


async def test_liveness_passes_with_no_schema_and_no_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path, TWO)
    monkeypatch.setenv("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/no-such-dir/x.db")
    async with _serve() as client:
        live = await client.get(HEALTHZ)
    assert live.status_code == OK


async def test_the_503_body_names_no_revision_url_or_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path / "old", [("0001", None)])
    await runner.run_migrations(Settings())
    _release(monkeypatch, tmp_path / "new", TWO)
    async with _serve() as client:
        res = await client.get(HEALTH)
    text = json.dumps(res.json())
    assert set(res.json()) == {"status", "reason"}
    for leaked in ("0001", "0002", "sqlite", str(tmp_path), "alembic", "Error"):
        assert leaked not in text


async def test_readiness_is_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _release(monkeypatch, tmp_path / "old", [("0001", None)])
    await runner.run_migrations(Settings())
    url = Settings().database_url or ""
    _release(monkeypatch, tmp_path / "new", TWO)
    async with _serve() as client:
        await client.get(HEALTH)
    assert revision_of(url) == "0001"


async def test_the_expected_head_is_computed_once_at_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _release(monkeypatch, tmp_path, TWO)
    await runner.run_migrations(Settings())
    calls: list[int] = []
    real = schema.load_revisions

    def counting() -> schema.Revisions:
        calls.append(1)
        return real()

    monkeypatch.setattr(schema, "load_revisions", counting)
    async with _serve() as client:
        for _ in range(3):
            await client.get(HEALTH)
    assert calls == [1]
