"""
Request logging middleware.

Logs every HTTP request with method, path, status code, and duration.
Useful for observability and debugging in development/staging.

Disabled in production by default (controlled by LOG_REQUESTS env var).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

logger = logging.getLogger(__name__)

# Only log requests when explicitly enabled (avoid noise in production).
_LOG_REQUESTS: bool = os.getenv("LOG_REQUESTS", "false").lower() == "true"


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log HTTP method, path, status code, and response time for every request.

    Notes
    -----
    - Controlled by `LOG_REQUESTS` environment variable (default: false).
    - Logs at INFO level with format: ``METHOD /path STATUS 0.123s``.
    - Skips logging for health-check endpoints to reduce noise.
    """

    _SKIP_PATHS: frozenset[str] = frozenset({"/healthz"})

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        """
        Intercept request, log on completion.

        Parameters
        ----------
        request : starlette.requests.Request
            Incoming HTTP request.
        call_next : callable
            Next middleware / route handler.

        Returns
        -------
        starlette.responses.Response
            The response from the downstream handler.
        """
        if not _LOG_REQUESTS:
            return await call_next(request)  # type: ignore[no-any-return]

        # Skip health-check endpoints to avoid log spam.
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)  # type: ignore[no-any-return]

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        logger.info(
            "%s %s %s %.3fs",
            request.method,
            request.url.path,
            response.status_code,
            duration,
        )
        return response  # type: ignore[no-any-return]
