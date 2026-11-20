# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for :class:`CorrelationIdMiddleware`.

Covers the pure-ASGI middleware:
- an inbound ``X-Request-Id`` header becomes the active id for the request's context
- a missing header still yields a real (generated) id — never blank
- the previous context is restored afterwards (no leak into the next request)
- non-HTTP scopes pass straight through untouched
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from your_service_name.backend.logging.correlation import NO_REQUEST_ID, get_request_id
from your_service_name.backend.middleware.correlation import CorrelationIdMiddleware

if TYPE_CHECKING:
    from starlette.types import Message, Receive, Scope, Send

# ASGI header names are lowercase (the server lowercases them); ``Headers.get`` matches
# case-insensitively, so the raw scope carries the lowercased form.
HEADER = b"x-request-id"


class _CaptureApp:
    """A fake ASGI app that records the active correlation id, then emits an empty response."""

    def __init__(self) -> None:
        self.seen: str | None = None

    async def __call__(self, _scope: Scope, _receive: Receive, send: Send) -> None:
        self.seen = get_request_id()
        await send({"type": "http.response.start", "status_code": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})


def _http_scope(headers: list[tuple[bytes, bytes]] | None = None) -> Scope:
    """Build a minimal ASGI http scope (headers as raw ``(name, value)`` byte pairs)."""
    return {
        "type": "http",
        "method": "GET",
        "headers": headers or [],
        "query_string": b"",
        "raw_path": b"/",
    }


async def _receive() -> Message:
    """Placeholder ASGI receive (the capture app never reads the stream)."""
    raise NotImplementedError


async def _sink(_message: Message) -> None:
    """Placeholder ASGI send (the capture app's response is discarded in the unit test)."""


class TestCorrelationIdMiddleware:
    """The middleware scopes the correlation id for each HTTP request."""

    @pytest.mark.asyncio
    async def test_reads_inbound_header(self) -> None:
        """A supplied ``X-Request-Id`` is carried into the request context."""
        capture = _CaptureApp()
        await CorrelationIdMiddleware(capture)(_http_scope([(HEADER, b"req-abc")]), _receive, _sink)
        assert capture.seen == "req-abc"

    @pytest.mark.asyncio
    async def test_generates_when_header_missing(self) -> None:
        """No header → a fresh, non-blank id is generated for the request."""
        capture = _CaptureApp()
        await CorrelationIdMiddleware(capture)(_http_scope(), _receive, _sink)
        seen = capture.seen
        assert seen is not None
        assert seen != NO_REQUEST_ID
        assert seen  # never blank

    @pytest.mark.asyncio
    async def test_resets_after_request(self) -> None:
        """The id is restored to the out-of-request sentinel once the request returns."""
        capture = _CaptureApp()
        await CorrelationIdMiddleware(capture)(_http_scope([(HEADER, b"req-1")]), _receive, _sink)
        # After the request, the outer context is back to the sentinel — no leak.
        assert get_request_id() == NO_REQUEST_ID

    @pytest.mark.asyncio
    async def test_ids_do_not_leak_between_requests(self) -> None:
        """Two sequential requests each get their own id; the first does not bleed into the next."""
        first = _CaptureApp()
        second = _CaptureApp()
        await CorrelationIdMiddleware(first)(_http_scope([(HEADER, b"req-A")]), _receive, _sink)
        await CorrelationIdMiddleware(second)(_http_scope(), _receive, _sink)
        assert first.seen == "req-A"
        assert second.seen != "req-A"
        assert second.seen is not None

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self) -> None:
        """A non-HTTP scope (e.g. lifespan) is forwarded untouched, no id set."""
        called = {"done": False}

        async def passthrough(_scope: Scope, _receive: Receive, _send: Send) -> None:
            called["done"] = True

        await CorrelationIdMiddleware(passthrough)({"type": "lifespan"}, _receive, _sink)
        assert called["done"] is True  # the inner app was invoked untouched


@pytest.mark.parametrize("value", [b"", b"explicit"])
@pytest.mark.asyncio
async def test_empty_header_is_replaced(value: bytes) -> None:
    """An empty header value is treated as missing and replaced with a generated id."""
    capture = _CaptureApp()
    await CorrelationIdMiddleware(capture)(_http_scope([(HEADER, value)]), _receive, _sink)
    seen = capture.seen
    assert seen is not None
    if value:
        assert seen == "explicit"
    else:
        assert seen != NO_REQUEST_ID
        assert seen  # never blank
