"""Correlation-id (``X-Request-Id``) ASGI middleware.

Reads the inbound ``X-Request-Id`` header (or generates one when the caller sends none) and makes
it the active correlation id for the request's context, so every log line emitted while handling
the request carries it (see :mod:`your_service_name.backend.logging.correlation`).

This is a *pure* ASGI middleware — not a ``BaseHTTPMiddleware`` — precisely so the correlation id is
set in the same context as the entire downstream handling (routers, services, the request-logging
middleware). A ``BaseHTTPMiddleware`` runs the endpoint in a separate AnyIO task, which would copy
the context before the id is set; wrapping the raw ASGI call keeps the id in scope for every log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.datastructures import Headers

from your_service_name.backend.logging.correlation import (
    HEADER_NAME,
    reset_request_id,
    set_request_id,
)

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


class CorrelationIdMiddleware:
    """Scope the correlation id for each HTTP request, then restore the previous context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Set the correlation id for the request and reset it afterwards (even on error)."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = set_request_id(Headers(scope=scope).get(HEADER_NAME))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_request_id(token)
