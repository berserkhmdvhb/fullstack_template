# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Opt-in OpenTelemetry tracing over OTLP/HTTP (``TRACING_ENABLED``, default off).

What it does
------------
- Server spans for the API (the FastAPI/ASGI instrumentation) and database spans (the SQLAlchemy
  instrumentation, on the app engine only). The probes and ``/metrics`` (``backend/paths.py``) are
  never traced.
- No request or response header and no body is captured, and the query string is removed from the
  span's URL attributes (it can carry tokens). A database span has the statement text with its
  placeholders, never a parameter value, and no SQL comment is added.
- Sampling is ``ParentBased(TraceIdRatioBased(TRACING_SAMPLE_RATIO))`` (only a server span may
  start a trace: a database call outside a traced request is dropped): a caller's ``traceparent``
  sampled flag is honoured (a client can force sampling of its own requests; the rate limit
  bounds that) and the ratio applies to new traces.
- Spans leave through a ``BatchSpanProcessor``: the export runs on its own thread, so an
  unreachable or failing collector costs a request nothing (a full queue drops spans). The
  lifespan flushes and shuts it down within ``SHUTDOWN_TIMEOUT_SECONDS``.
- The active span is the one source of ids: the log ``trace_id``/``span_id`` and the problem
  body's ``trace_id`` come from it (a valid inbound ``traceparent`` is continued, so they match
  the caller's trace). See ``logging/correlation.py``.

Every value is passed explicitly to the SDK constructors. What the SDK and the instrumentations
still read from the environment on their own is listed in ``wiki/backend-architecture.md``.

Everything OpenTelemetry-specific is imported inside the functions below: with tracing off none of
it is loaded (a test proves it).
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from your_service_name.backend import paths
from your_service_name.backend.logging.correlation import set_span_ids_provider
from your_service_name.backend.settings import parse_header_list

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from fastapi import FastAPI
    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SpanExporter
    from opentelemetry.sdk.trace.sampling import Sampler
    from opentelemetry.trace import Link, Span, SpanKind, TraceState
    from opentelemetry.util.types import Attributes
    from sqlalchemy.engine import Engine

    from your_service_name.backend.settings import Settings

logger = logging.getLogger(__name__)

#: The collector gets this long to take a batch; the batch thread waits, never a request.
EXPORT_TIMEOUT_SECONDS: Final[float] = 5.0
#: Flush and stop at shutdown for at most this long; the process then exits regardless.
SHUTDOWN_TIMEOUT_SECONDS: Final[float] = 5.0
_QUEUE_SIZE: Final[int] = 2048
_BATCH_SIZE: Final[int] = 512
_SCHEDULE_DELAY_MS: Final[int] = 5000
_TRACES_PATH: Final[str] = "/v1/traces"
#: Span attributes that can hold the URL (and so a query string), across semantic conventions.
_URL_ATTRIBUTES: Final[tuple[str, ...]] = ("http.url", "url.full", "http.target", "url.query")


def build_exporter(settings: Settings) -> SpanExporter:
    """The OTLP/HTTP span exporter for ``settings`` (tests replace this with an in-memory one)."""
    from opentelemetry.exporter.otlp.proto.http import Compression  # noqa: PLC0415
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
        OTLPSpanExporter,
    )

    raw = settings.OTEL_EXPORTER_OTLP_HEADERS
    return OTLPSpanExporter(
        endpoint=f"{settings.OTEL_EXPORTER_OTLP_ENDPOINT}{_TRACES_PATH}",
        headers=parse_header_list(raw.get_secret_value()) if raw is not None else {},
        timeout=EXPORT_TIMEOUT_SECONDS,
        compression=Compression.Gzip,
    )


def _server_roots_only(inner: Sampler) -> Sampler:
    """Wrap ``inner`` so only a SERVER span may start a trace.

    A database call outside a traced request (a probe's ``SELECT 1``, startup) would otherwise
    become a one-span trace of its own, which is noise and would undo the probe exclusion.
    """
    from opentelemetry import trace  # noqa: PLC0415
    from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult  # noqa: PLC0415

    class ServerRootsOnly(Sampler):
        def should_sample(  # noqa: PLR0913, PLR0917 — the SDK's signature
            self,
            parent_context: Context | None,
            trace_id: int,
            name: str,
            kind: SpanKind | None = None,
            attributes: Attributes = None,
            links: Sequence[Link] | None = None,
            trace_state: TraceState | None = None,
        ) -> SamplingResult:
            parent = trace.get_current_span(parent_context).get_span_context()
            if not parent.is_valid and kind != trace.SpanKind.SERVER:
                return SamplingResult(Decision.DROP)
            return inner.should_sample(
                parent_context, trace_id, name, kind, attributes, links, trace_state
            )

        def get_description(self) -> str:
            return f"ServerRootsOnly{{{inner.get_description()}}}"

    return ServerRootsOnly()


def _excluded_urls(settings: Settings) -> str:
    """Comma-separated regexes for the instrumentation: the exact exempt paths, query allowed."""
    return ",".join(
        re.escape(path) + r"(\?.*)?$" for path in sorted(paths.exempt_paths(settings.API_VERSION))
    )


def _strip_query(span: Span, scope: dict[str, Any]) -> None:  # noqa: ARG001
    """Server-request hook: the span carries the path, never the query string."""
    if not span.is_recording():
        return
    attributes: Mapping[str, object] = getattr(span, "attributes", None) or {}
    for name in _URL_ATTRIBUTES:
        value = attributes.get(name)
        if not isinstance(value, str):
            continue
        if name == "url.query":
            span.set_attribute(name, "")
        else:
            span.set_attribute(name, value.split("?", 1)[0])


def _active_span_ids() -> tuple[str, str, str] | None:
    """``(trace id, span id, flags)`` of the active valid span, as the lowercase hex of W3C."""
    from opentelemetry import trace  # noqa: PLC0415

    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x"), format(ctx.trace_flags, "02x")


@dataclass
class TracingHandle:
    """What the lifespan needs: instrument the database engine, flush and stop the exporter."""

    provider: TracerProvider
    _sqlalchemy_instrumented: bool = False

    def instrument_engine(self, engine: Engine) -> None:
        """Trace the app engine's statements (text only; no parameters, no SQL commenter)."""
        from opentelemetry.instrumentation.sqlalchemy import (  # noqa: PLC0415
            SQLAlchemyInstrumentor,
        )

        # ``skip_dep_check``: the instrumentation declares SQLAlchemy < 2.1 although the locked
        # 2.1 releases work with it; the test below runs the real path against the lock.
        SQLAlchemyInstrumentor().instrument(
            engine=engine,
            tracer_provider=self.provider,
            enable_commenter=False,
            skip_dep_check=True,
        )
        self._sqlalchemy_instrumented = True

    def shutdown(self) -> None:
        """Flush the queue and stop the exporter, giving up after ``SHUTDOWN_TIMEOUT_SECONDS``."""
        if self._sqlalchemy_instrumented:
            from opentelemetry.instrumentation.sqlalchemy import (  # noqa: PLC0415
                SQLAlchemyInstrumentor,
            )

            SQLAlchemyInstrumentor().uninstrument()
            self._sqlalchemy_instrumented = False
        set_span_ids_provider(None)
        worker = threading.Thread(target=self.provider.shutdown, daemon=True)
        worker.start()
        worker.join(SHUTDOWN_TIMEOUT_SECONDS)
        if worker.is_alive():
            logger.warning("Tracing exporter did not stop in time; pending spans are dropped.")


def install_tracing(app: FastAPI, settings: Settings) -> TracingHandle:
    """Build the provider and instrument ``app``; one call per process (per ``create_app``)."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased  # noqa: PLC0415

    provider = TracerProvider(
        sampler=_server_roots_only(ParentBased(TraceIdRatioBased(settings.TRACING_SAMPLE_RATIO))),
        # The constructor, not ``Resource.create``: that one also reads OTEL_RESOURCE_ATTRIBUTES.
        resource=Resource({"service.name": settings.OTEL_SERVICE_NAME}),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            build_exporter(settings),
            max_queue_size=_QUEUE_SIZE,
            schedule_delay_millis=_SCHEDULE_DELAY_MS,
            max_export_batch_size=_BATCH_SIZE,
            export_timeout_millis=int(EXPORT_TIMEOUT_SECONDS * 1000),
        )
    )
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=_excluded_urls(settings),
        server_request_hook=_strip_query,
        exclude_spans=["receive", "send"],
    )
    set_span_ids_provider(_active_span_ids)
    handle = TracingHandle(provider)
    app.state.tracing = handle
    logger.info(
        "Tracing enabled: spans go to the OTLP/HTTP collector, sample ratio %s",
        settings.TRACING_SAMPLE_RATIO,
    )
    return handle
