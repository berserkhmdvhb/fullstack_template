"""Request correlation id (``X-Request-Id``) propagation into logs.

A correlation id ties every log line emitted while handling a request back to that request —
essential once logs are shipped to a central system where thousands of requests are interleaved.
The id is read from the inbound ``X-Request-Id`` header (so a caller's own id is carried through
the whole stack) or generated when the caller sends none.

The active id lives in a :class:`contextvars.ContextVar`, so it is scoped to the request's async
context and never leaks between concurrent requests. :class:`RequestIdFilter` injects it into
every :class:`logging.LogRecord`, which is how application, request and audit logs all carry it
without any call site having to pass it explicitly.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar, Token

#: Header the correlation id is read from (and, where echoed, written back to).
HEADER_NAME: str = "X-Request-Id"

#: Value used when no request is in scope (startup/shutdown) or no id was supplied yet.
NO_REQUEST_ID: str = "-"

# The active correlation id for the current async context. ``-`` is the out-of-request default.
_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST_ID)


def generate_request_id() -> str:
    """Return a fresh, opaque correlation id (UUID4 hex, no dashes)."""
    return uuid.uuid4().hex


def get_request_id() -> str:
    """Return the correlation id active in this context (``-`` when none is set)."""
    return _REQUEST_ID.get()


def set_request_id(request_id: str | None) -> Token[str]:
    """Make ``request_id`` the active id for the current context and return the reset token.

    A missing or empty id is replaced with a freshly generated one, so downstream logs always
    carry a real id rather than an empty string. Restore it with :func:`reset_request_id` in a
    ``finally`` block.
    """
    return _REQUEST_ID.set(request_id or generate_request_id())


def reset_request_id(token: Token[str]) -> None:
    """Restore the previous correlation id from the token :func:`set_request_id` returned."""
    _REQUEST_ID.reset(token)


class RequestIdFilter(logging.Filter):
    """Inject the active correlation id into every log record as ``record.request_id``.

    Attach one instance to the root logger (or its handlers) at configuration time; from then on
    every log — application, request and audit — carries the id, and the JSON formatter surfaces
    it as a first-class field for log aggregation.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add ``request_id`` to the record; always returns ``True`` (it never suppresses)."""
        record.request_id = get_request_id()
        return True
