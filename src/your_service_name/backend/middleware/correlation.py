"""Correlation (``X-Request-Id``) and trace (``traceparent``) ASGI middleware.

Reads the inbound ``X-Request-Id`` header (keeping it only when it is at most 64 characters of
``[A-Za-z0-9._-]``, otherwise generating one) and makes it the active correlation id for the
request's context, so every log line emitted while handling the request carries it (see
:mod:`your_service_name.backend.logging.correlation`). The id in use is echoed on the response.

A valid W3C ``traceparent`` is parsed the same way (an invalid or absent one is ignored): its trace
id lands on every log line. No trace id is ever generated here.

This is a *pure* ASGI middleware — not a ``BaseHTTPMiddleware`` — precisely so the correlation id is
set in the same context as the entire downstream handling (routers, services, the request-logging
middleware). A ``BaseHTTPMiddleware`` runs the endpoint in a separate AnyIO task, which would copy
the context before the id is set; wrapping the raw ASGI call keeps the id in scope for every log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.datastructures import Headers, MutableHeaders

from your_service_name.backend.logging.correlation import (
    HEADER_NAME,
    TRACEPARENT_HEADER,
    TRACESTATE_HEADER,
    get_request_id,
    parse_traceparent,
    reset_request_id,
    reset_trace_context,
    set_request_id,
    set_trace_context,
)

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


class CorrelationIdMiddleware:
    """Scope the correlation id for each HTTP request, then restore the previous context."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Set the correlation id for the request and reset it afterwards (even on error)."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        token = set_request_id(headers.get(HEADER_NAME))
        request_id = get_request_id()
        # Exactly one traceparent header counts; a repeated one is ambiguous and ignored.
        values = headers.getlist(TRACEPARENT_HEADER)
        state_values = headers.getlist(TRACESTATE_HEADER)
        trace = (
            parse_traceparent(values[0], ",".join(state_values) if state_values else None)
            if len(values) == 1
            else None
        )
        trace_token = set_trace_context(trace)
        # The scope outlives the context variables (reset below), and the catch-all 500 handler runs
        # outside this middleware: leave the ids on the scope's shared ``state`` for it.
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["trace_context"] = trace
        state["trace_id"] = trace.trace_id if trace else None

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                MutableHeaders(scope=message)[HEADER_NAME] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            reset_trace_context(trace_token)
            reset_request_id(token)
