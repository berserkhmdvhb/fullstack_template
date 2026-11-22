# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Connection-pool gauges, read from the live pool at scrape time (no background task, no state).

A pool class without ``size()``/``checkedout()``/``checkedin()``/``overflow()`` (SQLite's
``StaticPool``/``NullPool``) yields no sample for the missing ones; a scrape never raises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from prometheus_client import REGISTRY
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.ext.asyncio import AsyncEngine

_GAUGES: Final[tuple[tuple[str, str, str], ...]] = (
    ("db_pool_size", "size", "Configured connection pool size."),
    ("db_pool_checked_out", "checkedout", "Pool connections in use."),
    ("db_pool_checked_in", "checkedin", "Idle connections in the pool."),
    ("db_pool_overflow", "overflow", "Current pool overflow, as SQLAlchemy reports it (signed)."),
)


class PoolCollector(Collector):
    """Yields the pool gauges of the engine ``get_engine`` returns (none for ``None``)."""

    def __init__(self, get_engine: Callable[[], AsyncEngine | None]) -> None:
        self._get_engine = get_engine

    def collect(self) -> Iterator[GaugeMetricFamily]:
        """One gauge per pool method that exists and answers."""
        engine = self._get_engine()
        pool: Any = getattr(getattr(engine, "sync_engine", None), "pool", None)
        for name, method, doc in _GAUGES:
            try:
                value = float(getattr(pool, method)())
            except Exception:  # noqa: BLE001, S112 - a scrape must never fail on a pool quirk
                continue
            gauge = GaugeMetricFamily(name, doc)
            gauge.add_metric([], value)
            yield gauge


_registered: PoolCollector | None = None


def register_pool_metrics(get_engine: Callable[[], AsyncEngine | None]) -> None:
    """Register the collector on the default registry once per process (later calls: no-op)."""
    # ruff: noqa: PLW0603 — once-per-process state.
    global _registered
    if _registered is None:
        _registered = PoolCollector(get_engine)
        REGISTRY.register(_registered)
