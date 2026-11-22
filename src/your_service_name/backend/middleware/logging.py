"""
Request logging middleware.

Logs every HTTP request with method, path, status code, and duration.
Useful for observability and debugging in development/staging.

Off unless the ``LOG_REQUESTS`` setting is on; ``create_app`` passes it in.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log HTTP method, path, status code, and response time for every request.

    Notes
    -----
    - Controlled by the ``log_requests`` argument (the `LOG_REQUESTS` setting, default: false).
    - Logs at INFO level with format: ``METHOD /path STATUS 0.123s client=IP``, where the IP is the
      one resolved by ``ClientIpMiddleware`` (the TCP peer unless it is a trusted proxy).
    - Skips the exact paths in ``skip_paths`` (the probes and metrics, from ``paths.exempt_paths``).
    - Control characters in the path are escaped (``\\x0a``), so a decoded ``%0A`` cannot forge a
      second line in the text format.
    - It never logs the query string, a header, a cookie or a body, so it is safe to leave on in a
      cluster. What a line may hold is pinned by ``tests/backend/middleware/test_logging_app.py``.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        log_requests: bool = False,
        skip_paths: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(app)
        self._log_requests = log_requests
        self._skip_paths = skip_paths

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

        # Skip the probes and the metrics scrape: they would be most of the log.
        if request.url.path in self._skip_paths:
            return await call_next(request)  # type: ignore[no-any-return]

        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        logger.info(
            "%s %s %s %.3fs client=%s",
            request.method,
            _CONTROL.sub(lambda m: f"\\x{ord(m[0]):02x}", request.url.path),
            response.status_code,
            duration,
            request.client.host if request.client else "-",
        )
        return response  # type: ignore[no-any-return]
