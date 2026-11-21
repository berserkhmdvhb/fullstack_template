"""Request correlation id (``X-Request-Id``) propagation into logs.

A correlation id ties every log line emitted while handling a request back to that request —
essential once logs are shipped to a central system where thousands of requests are interleaved.
The id is read from the inbound ``X-Request-Id`` header (so a caller's own id is carried through
the whole stack) or generated when the caller sends none.

The inbound id is kept only when it is at most 64 characters of ``[A-Za-z0-9._-]``; anything else
is replaced by a generated id and never logged (a rejected value could be a log-injection attempt).

A valid W3C ``traceparent`` header (version 00) is carried the same way: its trace id lands on every
log line as ``trace_id``, and :func:`outgoing_trace_headers` is the one helper that forwards it.

The active id lives in a :class:`contextvars.ContextVar`, so it is scoped to the request's async
context and never leaks between concurrent requests. :class:`RequestIdFilter` injects it into
every :class:`logging.LogRecord`, which is how application, request and audit logs all carry it
without any call site having to pass it explicitly.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

#: Header the correlation id is read from (and, where echoed, written back to).
HEADER_NAME: str = "X-Request-Id"

#: Value used when no request is in scope (startup/shutdown) or no id was supplied yet.
NO_REQUEST_ID: str = "-"

#: Header carrying the W3C Trace Context (level 1).
TRACEPARENT_HEADER: str = "traceparent"
TRACESTATE_HEADER: str = "tracestate"

_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_TRACEPARENT = re.compile(
    r"([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})(-.*)?", re.DOTALL
)
_TRACEPARENT_LENGTH = 55
_MAX_TRACESTATE = 512
_ZERO_TRACE, _ZERO_PARENT = "0" * 32, "0" * 16


@dataclass(frozen=True)
class TraceContext:
    """A parsed ``traceparent``: trace id, the caller's span id (``parent_id``) and flags."""

    trace_id: str
    parent_id: str
    flags: str
    tracestate: str | None = None


def parse_traceparent(value: str | None, tracestate: str | None = None) -> TraceContext | None:
    """Parse a ``traceparent`` value (W3C Trace Context level 1); ``None`` when it is invalid.

    Version ``00`` is exactly ``00-<32 hex trace id>-<16 hex parent id>-<2 hex flags>``. A newer
    version (``01`` to ``fe``) is read by its first 55 characters, which must be followed by a
    ``-`` or the end. ``ff`` is invalid, hex is lowercase and neither id may be all zeros. An
    invalid value is ignored, never an error. ``tracestate`` is kept as received when it is at
    most 512 characters.
    """
    match = _TRACEPARENT.fullmatch(value or "")
    if match is None:
        return None
    version, trace_id, parent_id, flags, rest = match.groups()
    if version == "ff" or trace_id == _ZERO_TRACE or parent_id == _ZERO_PARENT:
        return None
    if version == "00" and rest is not None:
        return None
    if rest is not None and len(value or "") <= _TRACEPARENT_LENGTH:
        return None
    kept = tracestate if tracestate and len(tracestate) <= _MAX_TRACESTATE else None
    return TraceContext(trace_id, parent_id, flags, kept)


# The active correlation id for the current async context. ``-`` is the out-of-request default.
_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST_ID)


# The active W3C trace context, from the inbound ``traceparent``.
_TRACE: ContextVar[TraceContext | None] = ContextVar("trace_context", default=None)

# With OpenTelemetry tracing on (``observability/tracing.py``) the active span is the one source of
# ids: this returns its ``(trace id, span id, flags)``, or ``None`` outside a valid span. ``None``
# here (tracing off) keeps the ``traceparent`` behaviour above.
_span_ids_provider: Callable[[], tuple[str, str, str] | None] | None = None


def set_span_ids_provider(provider: Callable[[], tuple[str, str, str] | None] | None) -> None:
    """Install (or, with ``None``, remove) the active-span lookup; tracing does this."""
    global _span_ids_provider  # noqa: PLW0603 — set at startup, cleared at shutdown
    _span_ids_provider = provider


def _span_ids() -> tuple[str, str, str] | None:
    return _span_ids_provider() if _span_ids_provider is not None else None


def generate_request_id() -> str:
    """Return a fresh, opaque correlation id (UUID4 hex, no dashes)."""
    return uuid.uuid4().hex


def get_request_id() -> str:
    """Return the correlation id active in this context (``-`` when none is set)."""
    return _REQUEST_ID.get()


def set_request_id(request_id: str | None) -> Token[str]:
    """Make ``request_id`` the active id for the current context and return the reset token.

    A missing, empty or invalid id (over 64 characters, or outside ``[A-Za-z0-9._-]``) is replaced
    with a freshly generated one, so downstream logs always carry a real, safe id. The rejected
    value is never logged. Restore it with :func:`reset_request_id` in a ``finally`` block.
    """
    if request_id is not None and _VALID_REQUEST_ID.fullmatch(request_id):
        return _REQUEST_ID.set(request_id)
    if request_id:
        logger.debug("Inbound %s was invalid and has been replaced", HEADER_NAME)
    return _REQUEST_ID.set(generate_request_id())


def reset_request_id(token: Token[str]) -> None:
    """Restore the previous correlation id from the token :func:`set_request_id` returned."""
    _REQUEST_ID.reset(token)


def get_trace_id() -> str | None:
    """The trace id of the current request.

    The active span's when tracing is on, else the one of a valid inbound ``traceparent``, else
    ``None``.
    """
    if ids := _span_ids():
        return ids[0]
    ctx = _TRACE.get()
    return ctx.trace_id if ctx else None


def get_span_id() -> str | None:
    """The id of the active span, or ``None`` (tracing off, or outside a span)."""
    ids = _span_ids()
    return ids[1] if ids else None


def set_trace_context(ctx: TraceContext | None) -> Token[TraceContext | None]:
    """Make ``ctx`` the active trace context; restore with :func:`reset_trace_context`."""
    return _TRACE.set(ctx)


def reset_trace_context(token: Token[TraceContext | None]) -> None:
    """Restore the previous trace context from the token :func:`set_trace_context` returned."""
    _TRACE.reset(token)


def outgoing_trace_headers() -> dict[str, str]:
    """Headers that carry the current trace to a downstream HTTP call (empty without a trace).

    Merge them into every outbound request, for example ``httpx.get(url, headers={**own,
    **outgoing_trace_headers()})``. It forwards the inbound trace (restarted at version 00) and its
    ``tracestate``, as received, when that is at most 512 characters. With tracing on, the parent
    is the active span (so the downstream service's span hangs under it).
    """
    ctx = _TRACE.get()
    if ids := _span_ids():
        headers = {TRACEPARENT_HEADER: f"00-{ids[0]}-{ids[1]}-{ids[2]}"}
        if ctx and ctx.tracestate and ctx.trace_id == ids[0]:
            headers[TRACESTATE_HEADER] = ctx.tracestate
        return headers
    if ctx is None:
        return {}
    headers = {TRACEPARENT_HEADER: f"00-{ctx.trace_id}-{ctx.parent_id}-{ctx.flags}"}
    if ctx.tracestate:
        headers[TRACESTATE_HEADER] = ctx.tracestate
    return headers


class RequestIdFilter(logging.Filter):
    """Inject the active correlation id into every log record as ``record.request_id``.

    Attach one instance to the root logger (or its handlers) at configuration time; from then on
    every log — application, request and audit — carries the id, and the JSON formatter surfaces
    it as a first-class field for log aggregation.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add ``request_id`` (and ``trace_id``, ``span_id`` when set); keeps every record."""
        record.request_id = get_request_id()
        trace_id = get_trace_id()
        span_id = get_span_id()
        record.trace_id = trace_id
        record.span_id = span_id
        record.trace_part = (f" trace_id={trace_id}" if trace_id else "") + (
            f" span_id={span_id}" if span_id else ""
        )
        return True
