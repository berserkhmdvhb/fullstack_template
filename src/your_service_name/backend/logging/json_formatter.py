"""Structured (JSON) log formatting for production.

In production logs are shipped to a central system (Fluentd → Elasticsearch/…) where they must be
machine-parseable, so each record is emitted as a single-line JSON object. In development the plain
``key=value``-friendly text format is kept for readability (see ``main._configure_logging``).

The formatter always carries ``@timestamp``, ``level``, ``logger``, ``message`` and ``request_id``
(the latter injected by :class:`~your_service_name.backend.logging.correlation.RequestIdFilter`);
``trace_id`` is added when the request carried a valid ``traceparent`` (or tracing is on), and
``span_id`` with tracing on. Any extra keys attached
at the call site via ``logger.info(..., extra={...})`` are merged in as structured fields, so
callers can add context (e.g. ``item_id``) without changing the format.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from your_service_name.backend.logging.correlation import NO_REQUEST_ID

# Keys the formatter already provides explicitly — never let ``extra=`` clobber them.
_RESERVED_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "request_id",
        "trace_id",
        "span_id",
        "trace_part",
    }
)


class JsonLogFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as compact JSON (one object per line, no trailing newline)."""
        payload: dict[str, Any] = {
            "@timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", NO_REQUEST_ID),
        }
        if trace_id := getattr(record, "trace_id", None):
            payload["trace_id"] = trace_id
        if span_id := getattr(record, "span_id", None):
            payload["span_id"] = span_id
        payload.update(
            {key: value for key, value in record.__dict__.items() if key not in _RESERVED_KEYS}
        )
        return json.dumps(payload, default=str)
