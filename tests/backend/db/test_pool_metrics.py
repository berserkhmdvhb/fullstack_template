# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The pool gauges: read from the live pool at scrape time, safe on pools that lack the methods."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool, StaticPool

from your_service_name.backend.db.pool_metrics import PoolCollector, register_pool_metrics

if TYPE_CHECKING:
    from pathlib import Path

NAMES = ("db_pool_size", "db_pool_checked_out", "db_pool_checked_in", "db_pool_overflow")
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


async def test_pools_without_the_methods_yield_no_samples_and_do_not_raise(tmp_path: Path) -> None:
    for pool in (StaticPool, NullPool):
        engine = _engine(tmp_path, poolclass=pool)
        try:
            samples = _scrape(engine)
        finally:
            await engine.dispose()
        assert set(samples) <= set(NAMES)
        if pool is NullPool:
            assert "db_pool_size" not in samples


def test_no_engine_yields_nothing() -> None:
    assert _scrape(None) == {}


def test_a_failing_pool_never_raises_at_scrape_time() -> None:
    class _Broken:
        class sync_engine:  # noqa: N801
            @staticmethod
            def pool() -> None: ...

    assert _scrape(_Broken()) == {}  # type: ignore[arg-type]


def test_registering_twice_does_not_raise_or_duplicate() -> None:
    register_pool_metrics(lambda: None)
    register_pool_metrics(lambda: None)
    collectors = [c for c in list(REGISTRY._collector_to_names) if isinstance(c, PoolCollector)]
    assert len(collectors) == 1
