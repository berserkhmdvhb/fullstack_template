# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The pool gauges: read from the live pool at scrape time, safe on pools that lack the methods."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from prometheus_client import CollectorRegistry
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool, StaticPool

from your_service_name.backend.db import pool_metrics
from your_service_name.backend.db.pool_metrics import PoolCollector, register_pool_metrics

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

NAMES = (
    "db_pool_max_overflow",
    "db_pool_size",
    "db_pool_checked_out",
    "db_pool_checked_in",
    "db_pool_overflow",
)
POOL_SIZE = 2


def _engine(tmp_path: Path, **kwargs: object) -> AsyncEngine:
    return create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p.db'}", **kwargs)


def _scrape(engine: AsyncEngine | None) -> dict[str, float]:
    return {m.name: m.samples[0].value for m in PoolCollector(lambda: engine).collect()}


async def test_a_queue_pool_reports_its_size_use_and_overflow(tmp_path: Path) -> None:
    engine = _engine(tmp_path, poolclass=AsyncAdaptedQueuePool, pool_size=POOL_SIZE, max_overflow=3)
    try:
        async with engine.connect():
            held = _scrape(engine)
        released = _scrape(engine)
    finally:
        await engine.dispose()
    assert held["db_pool_size"] == POOL_SIZE
    assert held["db_pool_checked_out"] == 1
    assert held["db_pool_checked_in"] == 0
    assert held["db_pool_overflow"] == -(POOL_SIZE - 1)
    assert released["db_pool_checked_out"] == 0
    assert released["db_pool_checked_in"] == 1


async def test_pools_without_the_methods_yield_no_samples(tmp_path: Path) -> None:
    for pool in (StaticPool, NullPool):
        engine = _engine(tmp_path, poolclass=pool)
        try:
            assert _scrape(engine) == {}
        finally:
            await engine.dispose()


def test_no_engine_yields_nothing() -> None:
    assert _scrape(None) == {}


class _FakePool:
    """``size`` answers; ``checkedout`` raises; ``checkedin`` answers a non-number."""

    _max_overflow = 4

    @staticmethod
    def size() -> int:
        return 5

    @staticmethod
    def checkedout() -> int:
        msg = "pool is gone"
        raise RuntimeError(msg)

    @staticmethod
    def checkedin() -> str:
        return "n/a"


def test_a_raising_or_non_numeric_method_is_skipped_and_the_scrape_does_not_raise() -> None:
    engine = SimpleNamespace(sync_engine=SimpleNamespace(pool=_FakePool()))
    assert _scrape(engine) == {"db_pool_size": 5, "db_pool_max_overflow": 4}  # type: ignore[arg-type]


async def test_the_configured_max_overflow_is_exported(tmp_path: Path) -> None:
    engine = _engine(tmp_path, poolclass=AsyncAdaptedQueuePool, pool_size=2, max_overflow=3)
    try:
        assert _scrape(engine)["db_pool_max_overflow"] == 3  # noqa: PLR2004
    finally:
        await engine.dispose()


def test_registering_twice_does_not_raise_or_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pool_metrics, "_registered", None)
    registry = CollectorRegistry()
    register_pool_metrics(lambda: None, registry)
    register_pool_metrics(lambda: None, registry)
    assert len(registry._collector_to_names) == 1
    assert set(registry._names_to_collectors) == set(NAMES)
