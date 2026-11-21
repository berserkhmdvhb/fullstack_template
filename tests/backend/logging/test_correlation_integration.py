# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
End-to-end: the correlation id set from the inbound ``X-Request-Id`` header reaches the log
records emitted while handling the request, through the real FastAPI stack.

This is the integration seam the unit tests can't reach on their own:
:class:`CorrelationIdMiddleware` is added outermost in ``create_app`` (just inside the passive
Prometheus metrics middleware) so it scopes the id for every log emitted while handling the
request — including the request-logging middleware (a ``BaseHTTPMiddleware`` that runs the
endpoint in a separate AnyIO task) and the route handlers. The test proves the id set by the
outer middleware survives that task boundary and lands on downstream records, so application,
request and audit logs all carry it without any call site passing it explicitly.

The capturing handler carries its own :class:`RequestIdFilter` (exactly as production's handler
does), so it observes the id the way a real log sink would.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

import pytest
from _pytest.logging import LogCaptureHandler

from your_service_name.backend.logging.correlation import NO_REQUEST_ID, RequestIdFilter
from your_service_name.backend.main import create_app
from your_service_name.backend.middleware import logging as request_logging
from your_service_name.backend.middleware.correlation import CorrelationIdMiddleware
from your_service_name.backend.middleware.logging import RequestLoggingMiddleware

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

HEALTH_PATH = "/api/v1/health"
REQUEST_ID_HEADER = "X-Request-Id"


@pytest.fixture
def captured() -> Iterator[LogCaptureHandler]:
    """A root-logger handler carrying the correlation-id filter, for the duration of the test.

    The DB/alembic startup reconfigures logging and leaves non-root loggers disabled at an
    effective ``WARNING`` level, which would silence the request-logging middleware's ``INFO``
    record; re-enable it here so that record is observable. The previous state is restored on
    teardown, and request logging is switched on by building the app with ``LOG_REQUESTS=true``.
    """
    root = logging.getLogger()
    previous_root_level = root.level
    root.setLevel(logging.DEBUG)
    request_logging.logger.setLevel(logging.DEBUG)
    request_logging.logger.disabled = False
    handler = LogCaptureHandler()
    handler.setLevel(logging.DEBUG)
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_root_level)
        request_logging.logger.setLevel(logging.NOTSET)


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """The real app, built with request logging on so the per-request log line is emitted."""
    monkeypatch.setenv("LOG_REQUESTS", "true")
    return create_app()


def _request_ids(records: list[logging.LogRecord]) -> set[str | None]:
    """The correlation ids carried by the captured records (``None`` when unset)."""
    return {getattr(record, "request_id", None) for record in records}


@pytest.mark.asyncio
@pytest.mark.usefixtures("migrated_db")
async def test_request_id_reaches_downstream_logs(
    async_client: httpx.AsyncClient,
    captured: LogCaptureHandler,
) -> None:
    """A request carrying ``X-Request-Id`` produces log records with that id — through the stack."""
    request_id = "integration-req-123"
    response = await async_client.get(HEALTH_PATH, headers={REQUEST_ID_HEADER: request_id})
    assert response.is_success

    # The request-logging middleware logged within the request context, carrying the header id.
    assert request_id in _request_ids(captured.records)


@pytest.mark.asyncio
async def test_generated_id_when_header_absent(
    async_client: httpx.AsyncClient,
    captured: LogCaptureHandler,
) -> None:
    """With no inbound header, a generated (non-blank) id is carried onto the request's logs."""
    await async_client.get(HEALTH_PATH)

    ids = _request_ids(captured.records)
    ids.discard(NO_REQUEST_ID)  # any out-of-request records use the sentinel
    assert ids  # at least one request-scoped record was captured
    # A generated id is a UUID4 hex string — never the sentinel and never blank.
    assert all(record_id and record_id != NO_REQUEST_ID for record_id in ids)


def test_correlation_middleware_scopes_request_logging(app: FastAPI) -> None:
    """``CorrelationIdMiddleware`` sits outside ``RequestLoggingMiddleware`` in the stack.

    This is the wiring the two behavioral tests rely on: the id must be set *before* the
    request-logging middleware runs for its records to carry it.
    """
    # ``mw.cls`` is typed as a generic middleware factory; cast to ``type`` so ``list.index``
    # type-checks against the concrete middleware classes.
    classes = [cast("type", mw.cls) for mw in app.user_middleware]
    corr = classes.index(CorrelationIdMiddleware)
    log = classes.index(RequestLoggingMiddleware)
    assert corr < log
