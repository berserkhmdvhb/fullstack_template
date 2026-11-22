"""Request limits: a body-size cap and a trusted-host check, both pure ASGI.

Both answer with the RFC 9457 problem body (``errors.problem_response``), carrying the request id
because they sit just inside the correlation middleware (see ``main.create_app``).

- :class:`RequestSizeLimitMiddleware`: 413 when ``Content-Length`` is over the limit, and also when
  a body without a length (chunked) streams past it. ``MAX_REQUEST_BODY_BYTES`` defaults to the
  chart's ingress ``proxy-body-size``, so the edge and the service agree.
- :class:`TrustedHostMiddleware`: 400 unless the ``Host`` header matches ``ALLOWED_HOSTS``. Probe
  paths are exempt (the rate limiter's exact paths): Kubernetes probes send the Pod IP as ``Host``.
  Starlette's own middleware answers in plain text, hence this one.
"""

from __future__ import annotations

import contextlib
import ipaddress
import logging
import re
from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException

from your_service_name.backend import paths
from your_service_name.backend.errors import problem_response
from your_service_name.utils.versioning_api import DEFAULT_API_VERSION

if TYPE_CHECKING:
    from collections.abc import Sequence

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

_TOO_LARGE = 413
_BAD_REQUEST = 400
_BODY_DETAIL = "The request body is too large."
_HOST_DETAIL = "The Host header is not allowed."


_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# Host: an RFC 1123 hostname (an IPv4 address is one), or a bracketed IPv6 literal, then an
# optional numeric port. Anything else is not a Host this service can match, and is refused.
_HOST = re.compile(
    rf"(?P<host>(?=.{{1,253}}(?::|$))(?:{_LABEL}(?:\.{_LABEL})*)|\[[0-9a-f:.]+\])(?::\d{{1,5}})?"
)


def _hostname(header: str | None) -> str | None:
    """The lowercase host of a well-formed ``Host`` header, without its port; else ``None``."""
    if not header:
        return None
    match = _HOST.fullmatch(header.strip().lower())
    if match is None:
        return None
    host = match["host"]
    if host.startswith("["):
        try:
            ipaddress.IPv6Address(host[1:-1])
        except ValueError:
            return None
    return host


class PayloadTooLargeError(HTTPException):
    """Raised from ``receive`` once a streamed body passes the limit (handled as a 413 problem)."""

    def __init__(self) -> None:
        super().__init__(status_code=_TOO_LARGE, detail=_BODY_DETAIL)


class _BodyGuard:
    """The per-request state: counts streamed bytes, and replaces any answer once over the limit."""

    def __init__(self, scope: Scope, receive: Receive, send: Send, max_bytes: int) -> None:
        self._scope, self._receive, self._send, self._max = scope, receive, send, max_bytes
        self.seen = 0
        self.exceeded = False
        self._answered = False

    async def receive(self) -> Message:
        message = await self._receive()
        if message["type"] == "http.request":
            self.seen += len(message.get("body", b""))
            if self.seen > self._max:
                self.exceeded = True
                raise PayloadTooLargeError
        return message

    async def send(self, message: Message) -> None:
        # Once the limit is passed, whatever the app answers is replaced by the 413: FastAPI turns a
        # failed body read into a 400, and an inner middleware may wrap the error.
        if self.exceeded:
            await self.refuse()
        else:
            await self._send(message)

    async def refuse(self) -> None:
        if not self._answered:
            self._answered = True
            await problem_response(self._scope, _TOO_LARGE, _BODY_DETAIL)(
                self._scope, self._receive, self._send
            )


class RequestSizeLimitMiddleware:
    """Reject a body over ``max_bytes``: early on ``Content-Length``, else while it streams."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self._max = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer 413 for an oversized request; pass everything else through."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        guard = _BodyGuard(scope, receive, send, self._max)
        declared = Headers(scope=scope).get("content-length", "")
        if declared.isascii() and declared.isdigit() and int(declared) > self._max:
            await guard.refuse()
            return
        # ``suppress`` takes a bare error and an ``ExceptionGroup`` alike: a ``BaseHTTPMiddleware``
        # further in runs ``receive`` in a task group, which wraps what ``guard.receive`` raises.
        with contextlib.suppress(PayloadTooLargeError):
            await self.app(scope, guard.receive, guard.send)
        if guard.exceeded:
            await guard.refuse()


class TrustedHostMiddleware:
    """Answer a 400 problem unless ``Host`` matches ``allowed_hosts`` (``*.example.com`` works)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: Sequence[str],
        exempt_paths: frozenset[str] = paths.exempt_paths(DEFAULT_API_VERSION),
    ) -> None:
        self.app = app
        patterns = [p.strip().lower() for p in allowed_hosts]
        self._patterns = patterns
        self._allow_any = "*" in patterns
        self._exempt = exempt_paths

    def _allowed(self, host: str) -> bool:
        return any(
            host == p or (p.startswith("*.") and host.endswith(p[1:])) for p in self._patterns
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass an allowed or exempt request on; answer the rest with 400."""
        if self._allow_any or scope["type"] != "http" or scope["path"] in self._exempt:
            await self.app(scope, receive, send)
            return
        host = _hostname(Headers(scope=scope).get("host"))
        if host is not None and self._allowed(host):
            await self.app(scope, receive, send)
            return
        # The rejected value is attacker-controlled: never log it.
        logger.info("Request refused: Host header not in ALLOWED_HOSTS")
        await problem_response(scope, _BAD_REQUEST, _HOST_DETAIL)(scope, receive, send)
