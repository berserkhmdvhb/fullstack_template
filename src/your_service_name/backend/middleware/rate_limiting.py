# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Rate limiting middleware.

Provides a configurable, per-IP default rate limit applied to every route, with
liveness/readiness/metrics paths exempt (exact paths, see ``backend/paths.py``).

Why this is not slowapi: slowapi's middleware decides which limits apply by
resolving the target route *handler* from the top-level Starlette route table.
FastAPI assembles the versioned API under a nested router that slowapi's handler
lookup does not descend into, so its global ``default_limits`` were never checked
against any API request — the limiter was configured but never enforced (a
no-op). This middleware enforces the limit directly against the ``limits``
backend keyed by client IP, so it works regardless of how routes are nested.

Usage
-----
from your_service_name.backend.middleware.rate_limiting import register_rate_limiting
register_rate_limiting(app, settings)

Configuration
-------------
- ``RATE_LIMIT_DEFAULT`` (a ``Settings`` field): limit string in ``limits`` format
  (e.g. ``100/minute``, ``10/second``). Default ``100/minute``.
- ``RATE_LIMIT_STORAGE``: ``memory://`` (per replica) or Redis; see ``backend/rate_limiter.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from limits import parse
from starlette.middleware.base import BaseHTTPMiddleware

from your_service_name.backend import paths
from your_service_name.backend.errors import problem_response
from your_service_name.backend.rate_limiter import RateLimiter, build_storage
from your_service_name.backend.settings import (
    DEFAULT_RATE_LIMIT,
    DEFAULT_SERVICE_NAME,
    MEMORY_STORAGE_URL,
)
from your_service_name.utils.versioning_api import DEFAULT_API_VERSION

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI
    from limits import RateLimitItem
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

    from your_service_name.backend.settings import Settings

logger = logging.getLogger(__name__)

#: The key namespace of the default limit (the CSP report limit has its own).
DEFAULT_NAMESPACE = "default"


def _parse_limit(raw: str) -> RateLimitItem:
    """
    Parse a ``limits``-format rate limit string into a rate-limit item.

    Parameters
    ----------
    raw : str
        Limit spec, e.g. ``"100/minute"`` or ``"10/second"``.

    Returns
    -------
    limits.RateLimitItem
        The parsed limit (amount + granularity).

    Raises
    ------
    ValueError
        If ``raw`` is not a valid ``limits`` spec — fail loud at startup rather
        than silently serving with no limit.
    """
    try:
        return parse(raw)
    except ValueError as exc:
        msg = f"Invalid RATE_LIMIT_DEFAULT {raw!r}: {exc}"
        raise ValueError(msg) from exc


def _client_ip(request: Request) -> str:
    """Best-effort client IP used as the rate-limit key (falls back to loopback)."""
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Enforce a per-IP default rate limit on every non-exempt route.

    slowapi's ``BaseHTTPMiddleware`` subclass could not be used here because its
    enforcement resolves limits by route handler, which does not work with the
    nested API router (see the module docstring). This middleware keys on client
    IP and exempts the configured health/metrics paths.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: str = DEFAULT_RATE_LIMIT,
        exempt_paths: frozenset[str] = paths.exempt_paths(DEFAULT_API_VERSION),
        limiter: RateLimiter | None = None,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter or RateLimiter(
            build_storage(MEMORY_STORAGE_URL, 1), prefix=DEFAULT_SERVICE_NAME, timeout=1
        )
        self._limit = _parse_limit(limit)
        self._exempt_paths = exempt_paths

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Skip exempt paths; reject requests that exceed the per-IP limit with 429."""
        if request.url.path in self._exempt_paths:
            return await call_next(request)

        if not await self._limiter.hit(self._limit, DEFAULT_NAMESPACE, _client_ip(request)):
            retry_after = self._limit.GRANULARITY.seconds * self._limit.multiples
            return problem_response(
                request.scope,
                429,
                "Rate limit exceeded. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)


def register_rate_limiting(app: FastAPI, settings: Settings) -> None:
    """
    Configure and register the rate limiter on the FastAPI application.

    Parses ``RATE_LIMIT_DEFAULT`` (raising at startup on a bad value), installs a
    fixed-window, per-IP limiter on the configured storage (``RATE_LIMIT_STORAGE``, see
    ``backend/rate_limiter.py``), and mounts the enforcing middleware so the limit actually
    applies to routes. Liveness/readiness probes
    (``/health``, ``/healthz``) and ``/metrics`` are exempt.

    Parameters
    ----------
    app : fastapi.FastAPI
        The application to configure.
    settings : Settings
        Source of ``RATE_LIMIT_DEFAULT`` (already validated).

    Notes
    -----
    - Rate limit is controlled by the ``RATE_LIMIT_DEFAULT`` setting.
    - Key: the client IP resolved by ``ClientIpMiddleware`` (the TCP peer unless trusted proxy).
    - The 429 response is a problem+json body (with a ``Retry-After`` header) for API consistency.
    """
    limit = settings.RATE_LIMIT_DEFAULT
    logger.info("Rate limiting enabled: %s per IP", limit)
    # Parse eagerly so a misconfigured limit fails at startup, not per request.
    _parse_limit(limit)
    # One limiter (one storage) per app, shared with the other limited endpoints (the CSP report).
    limiter = RateLimiter(
        build_storage(
            settings.RATE_LIMIT_STORAGE.get_secret_value(),
            settings.RATE_LIMIT_STORAGE_TIMEOUT_SECONDS,
        ),
        prefix=DEFAULT_SERVICE_NAME,
        timeout=settings.RATE_LIMIT_STORAGE_TIMEOUT_SECONDS,
    )
    app.state.rate_limiter = limiter
    app.add_middleware(
        RateLimitMiddleware,
        limit=limit,
        limiter=limiter,
        exempt_paths=paths.exempt_paths(settings.API_VERSION),
    )
