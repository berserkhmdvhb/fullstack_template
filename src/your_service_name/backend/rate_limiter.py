# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
The rate limiter shared by the default limit and the CSP report limit: async, fail-open.

Storage is the async ``limits`` API (``limits.aio``), so a Redis call never blocks the event loop:

- ``memory://`` is ``limits.aio.storage.MemoryStorage`` (a counter per replica).
- ``redis://``, ``rediss://`` and ``redis+unix://`` are ``limits.aio.storage.RedisStorage`` with
  ``implementation="redispy"``, the redis-py asyncio client (the only extra package this needs;
  the default implementation would need ``coredis``). See the ``limits`` documentation,
  "Storage backends" -> "Async Storage" -> ``RedisStorage``: https://limits.readthedocs.io/

A storage error or a call slower than ``RATE_LIMIT_STORAGE_TIMEOUT_SECONDS`` allows the request
(fail open: a Redis outage must not take the API down) and logs a warning at most once per
``warn_interval`` seconds, with the exception type only (a message can carry the URL).

Keys are ``<prefix>/<namespace>/<client>`` plus the limit, so several services can share one Redis
(the prefix is the service name) and the default limit and the CSP limit never share a counter.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from limits.aio.storage import MemoryStorage
from limits.aio.strategies import FixedWindowRateLimiter
from prometheus_client import Counter

if TYPE_CHECKING:
    from limits import RateLimitItem
    from limits.aio.storage import Storage
    from starlette.requests import Request

logger = logging.getLogger(__name__)

#: Requests let through because the storage failed: the limit is not applying while this grows.
STORAGE_FAILURES = Counter(
    "rate_limit_storage_failures_total",
    "Rate-limit storage failures that let a request through (fail open).",
    ["reason"],
)

DEFAULT_WARN_INTERVAL_SECONDS: Final[float] = 60.0
_REDIS_SCHEMES: Final[frozenset[str]] = frozenset({"redis", "rediss", "redis+unix"})


def client_key(request: Request) -> str:
    """The rate-limit key of a request: the client IP ``ClientIpMiddleware`` resolved (loopback
    when the server gave none)."""
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


def build_storage(url: str, timeout: float) -> Storage:
    """The async storage for a ``RATE_LIMIT_STORAGE`` URL; it connects on first use, not here."""
    try:
        scheme = urlsplit(url).scheme
    except ValueError:
        scheme = ""
    if scheme == "memory":
        return MemoryStorage()
    if scheme in _REDIS_SCHEMES:
        # Imported here: with the default memory storage ``redis`` is never loaded.
        from limits.aio.storage import RedisStorage  # noqa: PLC0415

        try:
            return RedisStorage(
                url,
                implementation="redispy",
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
            )
        except Exception:  # noqa: BLE001, S110 — see below: nothing of it may be kept
            pass
        # Raised outside the handler: ``limits`` puts the whole URL (password included) in its
        # errors, and even ``from None`` leaves it reachable as ``__context__``.
        msg = "RATE_LIMIT_STORAGE could not be configured"
        raise ValueError(msg)
    msg = "RATE_LIMIT_STORAGE must start with memory://, redis://, rediss:// or redis+unix://"
    raise ValueError(msg)


class RateLimiter:
    """A fixed-window limiter over one storage, keyed by service prefix, namespace and client."""

    def __init__(
        self,
        storage: Storage,
        *,
        prefix: str,
        timeout: float,
        warn_interval: float = DEFAULT_WARN_INTERVAL_SECONDS,
    ) -> None:
        self._limiter = FixedWindowRateLimiter(storage)
        self._prefix = prefix
        self._timeout = timeout
        self._warn_interval = warn_interval
        self._last_warning: float | None = None

    async def hit(self, item: RateLimitItem, namespace: str, client: str) -> bool:
        """Count one hit: ``False`` over the limit, ``True`` under it or when the storage fails."""
        try:
            async with asyncio.timeout(self._timeout):
                return await self._limiter.hit(item, self._prefix, namespace, client)
        except Exception as exc:  # noqa: BLE001 — fail open on anything the storage raises
            # The cap above, or the client's own socket timeout (also on a starved event loop).
            timed_out = isinstance(exc, TimeoutError) or type(exc).__name__ == "TimeoutError"
            STORAGE_FAILURES.labels(reason="timeout" if timed_out else "error").inc()
            self._warn(exc)
            return True

    def _warn(self, exc: BaseException) -> None:
        now = time.monotonic()
        if self._last_warning is None or now - self._last_warning >= self._warn_interval:
            self._last_warning = now
            logger.warning(
                "Rate-limit storage unavailable (%s); allowing requests until it recovers",
                type(exc).__name__,
            )
