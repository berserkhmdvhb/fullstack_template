# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Rate limiting middleware.

Provides a configurable, per-IP default rate limit applied to every route, with
liveness/readiness/metrics paths exempt.

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
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from your_service_name.backend.settings import DEFAULT_RATE_LIMIT

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI
    from limits import RateLimitItem
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

    from your_service_name.backend.settings import Settings

logger = logging.getLogger(__name__)

# Liveness/readiness probes and the metrics endpoint are exempt from rate
# limiting. Matched by *trailing path segment* so the exemption holds regardless
# of the API prefix an adopter mounts under (e.g. /health vs /api/v1/health).
_DEFAULT_EXEMPT_PATH_SUFFIXES: tuple[str, ...] = ("health", "healthz", "metrics")


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


def _is_exempt_path(path: str, suffixes: tuple[str, ...]) -> bool:
    """
    Return True if ``path`` ends on one of the exempt trailing segments.

    Matches both a bare ``/health`` and a prefixed ``/api/v1/health`` (and
    ``/metrics``), so the exemption is independent of the mounted API prefix.
    """
    if not suffixes:
        return False
    return any(path == "/" + suffix or path.endswith("/" + suffix) for suffix in suffixes)


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
        exempt_path_suffixes: tuple[str, ...] = _DEFAULT_EXEMPT_PATH_SUFFIXES,
    ) -> None:
        super().__init__(app)
        self._limiter = FixedWindowRateLimiter(MemoryStorage())
        self._limit = _parse_limit(limit)
        self._exempt_path_suffixes = exempt_path_suffixes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Skip exempt paths; reject requests that exceed the per-IP limit with 429."""
        if _is_exempt_path(request.url.path, self._exempt_path_suffixes):
            return await call_next(request)

        if not self._limiter.hit(self._limit, _client_ip(request)):
            retry_after = self._limit.GRANULARITY.seconds * self._limit.multiples
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Please try again later."},
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)


def register_rate_limiting(app: FastAPI, settings: Settings) -> None:
    """
    Configure and register the rate limiter on the FastAPI application.

    Parses ``RATE_LIMIT_DEFAULT`` (raising at startup on a bad value), installs a
    fixed-window, in-memory, per-IP limiter, and mounts the enforcing middleware
    so the limit actually applies to routes. Liveness/readiness probes
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
    - Key: client IP address.
    - The 429 response is JSON (with a ``Retry-After`` header) for API consistency.
    """
    limit = settings.RATE_LIMIT_DEFAULT
    logger.info("Rate limiting enabled: %s per IP", limit)
    # Parse eagerly so a misconfigured limit fails at startup, not per request.
    _parse_limit(limit)
    app.add_middleware(RateLimitMiddleware, limit=limit)
