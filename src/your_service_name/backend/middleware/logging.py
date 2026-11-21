"""
Request logging middleware.

Logs every HTTP request with method, path, status code, and duration.
Useful for observability and debugging in development/staging.

Off unless the ``LOG_REQUESTS`` setting is on; ``create_app`` passes it in.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log HTTP method, path, status code, and response time for every request.

    Notes
    -----
    - Controlled by the ``log_requests`` argument (the `LOG_REQUESTS` setting, default: false).
    - Logs at INFO level with format: ``METHOD /path STATUS 0.123s``.
    - Skips logging for health-check endpoints to reduce noise.
    """

    _SKIP_PATHS: frozenset[str] = frozenset({"/healthz"})

    def __init__(self, app: ASGIApp, *, log_requests: bool = False) -> None:
        super().__init__(app)
        self._log_requests = log_requests

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
        if not self._log_requests:
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
