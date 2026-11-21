# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Opt-in tracing: spans, exclusions, what is (not) captured, one trace id, failing exporters."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from _pytest.logging import LogCaptureHandler
from asgi_lifespan import LifespanManager
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from pydantic import SecretStr

from your_service_name.backend.logging.correlation import RequestIdFilter
from your_service_name.backend.main import create_app
from your_service_name.backend.observability import tracing
from your_service_name.backend.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI
    from opentelemetry.sdk.trace import ReadableSpan

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT_ID = "00f067aa0ba902b7"
SENTINEL = "SENTINEL-value-42"
ENDPOINT = "http://collector.invalid:4318"


def _settings(*, ratio: float = 1.0, name: str = "your_service_name") -> Settings:
    return Settings(
        TRACING_ENABLED=True,
        OTEL_EXPORTER_OTLP_ENDPOINT=ENDPOINT,
        LOG_REQUESTS=True,
        TRACING_SAMPLE_RATIO=ratio,
        OTEL_SERVICE_NAME=name,
    )


@pytest.fixture(autouse=True)
def _reset_tracing_state() -> Iterator[None]:
    """Process-wide tracing state (the app count, the span-id hook) starts and ends clean."""
    from opentelemetry import propagate  # noqa: PLC0415
    from opentelemetry.trace.propagation.tracecontext import (  # noqa: PLC0415
        TraceContextTextMapPropagator,
    )

    from your_service_name.backend.logging import correlation  # noqa: PLC0415

    def reset() -> None:
        tracing._active_handles = 0
        correlation.set_span_ids_provider(None)
        propagate.set_global_textmap(TraceContextTextMapPropagator())

    reset()
    yield
    reset()


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    memory = InMemorySpanExporter()
    monkeypatch.setattr(tracing, "build_exporter", lambda _settings: memory)
    return memory


@pytest.fixture
def captured() -> Iterator[LogCaptureHandler]:
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.DEBUG)
    handler = LogCaptureHandler()
    handler.setLevel(logging.DEBUG)
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)
    for name in ("your_service_name.backend.middleware.logging",):
        logging.getLogger(name).disabled = False
        logging.getLogger(name).setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)


async def _run(app: FastAPI, method: str, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.request(method, path, **kwargs)
    return response


def _spans(exporter: InMemorySpanExporter, app: FastAPI) -> list[ReadableSpan]:
    app.state.tracing.provider.force_flush()
    return list(exporter.get_finished_spans())


def _values(spans: list[ReadableSpan]) -> str:
    return " ".join(f"{k}={v!s}" for s in spans for k, v in (s.attributes or {}).items())


@pytest.mark.usefixtures("migrated_db")
class TestServerSpans:
    async def test_a_request_makes_one_server_span(self, exporter: InMemorySpanExporter) -> None:
        app = create_app(_settings())
        response = await _run(app, "GET", "/api/v1/items")
        assert response.status_code == httpx.codes.OK
        servers = [s for s in _spans(exporter, app) if s.kind == SpanKind.SERVER]
        assert len(servers) == 1
        assert servers[0].resource.attributes["service.name"] == "your_service_name"

    @pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/healthz", "/metrics"])
    async def test_probes_and_metrics_are_not_traced(
        self, exporter: InMemorySpanExporter, path: str
    ) -> None:
        app = create_app(_settings())
        await _run(app, "GET", path)
        assert _spans(exporter, app) == []

    async def test_the_service_name_setting_is_used(self, exporter: InMemorySpanExporter) -> None:
        app = create_app(_settings(name="svc-x"))
        await _run(app, "GET", "/api/v1/items")
        span = _spans(exporter, app)[0]
        assert span.resource.attributes["service.name"] == "svc-x"

    async def test_no_headers_query_or_body_are_captured(
        self, exporter: InMemorySpanExporter
    ) -> None:
        app = create_app(_settings())
        await _run(
            app,
            "POST",
            f"/api/v1/echo?token={SENTINEL}",
            headers={"Authorization": f"Bearer {SENTINEL}", "Cookie": f"s={SENTINEL}"},
            json={"message": SENTINEL},
        )
        spans = _spans(exporter, app)
        assert spans
        assert SENTINEL not in _values(spans)
        assert not [k for s in spans for k in (s.attributes or {}) if "header" in k]

    async def test_a_database_span_has_statement_text_but_never_parameters(
        self, exporter: InMemorySpanExporter
    ) -> None:
        app = create_app(_settings())
        response = await _run(app, "POST", "/api/v1/items", json={"name": SENTINEL})
        assert response.status_code == httpx.codes.CREATED, response.text
        spans = _spans(exporter, app)
        statements = [
            str(a)
            for s in spans
            if s.kind == SpanKind.CLIENT
            for k, a in (s.attributes or {}).items()
            if k in {"db.statement", "db.query.text"}
        ]
        assert any("INSERT" in text.upper() for text in statements)
        assert SENTINEL not in _values(spans)
        assert not any("/*" in text for text in statements)  # no SQL commenter


@pytest.mark.usefixtures("migrated_db")
class TestCaptureAndExclusion:
    @pytest.mark.parametrize(
        "name",
        [
            "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST",
            "OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_RESPONSE",
        ],
    )
    async def test_the_environment_cannot_switch_header_capture_on(
        self, exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        monkeypatch.setenv(name, ".*")
        app = create_app(_settings())
        await _run(app, "GET", "/api/v1/items", headers={"X-Probe": "zq-secret-zq"})
        spans = _spans(exporter, app)
        assert spans
        assert not [k for s in spans for k in (s.attributes or {}) if "header" in k]
        assert "zq-secret-zq" not in _values(spans)

    @pytest.mark.parametrize(
        "path", ["/api/v1/items/metrics", "/x/health", "/api/v1/items/healthz"]
    )
    async def test_only_the_exact_probe_paths_are_excluded(
        self, exporter: InMemorySpanExporter, path: str
    ) -> None:
        app = create_app(_settings())
        await _run(app, "GET", path)
        assert [s for s in _spans(exporter, app) if s.kind == SpanKind.SERVER]

    async def test_the_probe_with_a_query_is_still_excluded(
        self, exporter: InMemorySpanExporter
    ) -> None:
        app = create_app(_settings())
        await _run(app, "GET", "/api/v1/healthz?x=1")
        assert _spans(exporter, app) == []


class TestProcessWideState:
    @pytest.mark.usefixtures("migrated_db")
    async def test_the_hook_lives_until_the_last_started_app_shuts_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        second_exporter = InMemorySpanExporter()
        exporters = iter([InMemorySpanExporter(), second_exporter])
        monkeypatch.setattr(tracing, "build_exporter", lambda _settings: next(exporters))
        from your_service_name.backend.logging import correlation  # noqa: PLC0415

        first, second = create_app(_settings()), create_app(_settings())
        assert correlation._span_ids_provider is None  # built, not started: nothing installed
        async with LifespanManager(first):
            async with LifespanManager(second):
                assert correlation._span_ids_provider is not None
                transport = httpx.ASGITransport(app=second)
                async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                    response = await client.post("/api/v1/items", json={"name": "zq-two"})
                assert response.status_code == httpx.codes.CREATED
                spans = _spans(second_exporter, second)
            assert correlation._span_ids_provider is not None  # the first is still running
        assert correlation._span_ids_provider is None
        assert any(s.kind == SpanKind.CLIENT for s in spans)
        assert [s for s in spans if s.kind == SpanKind.SERVER]

    @pytest.mark.usefixtures("exporter")
    def test_an_app_built_but_never_started_leaves_no_process_state(self) -> None:
        from your_service_name.backend.logging import correlation  # noqa: PLC0415

        create_app(_settings())
        create_app(_settings())
        assert tracing._active_handles == 0
        assert correlation._span_ids_provider is None

    @pytest.mark.usefixtures("exporter")
    def test_the_w3c_propagator_is_set_whatever_the_environment_says(self) -> None:
        from opentelemetry import propagate  # noqa: PLC0415
        from opentelemetry.propagators.composite import CompositePropagator  # noqa: PLC0415

        propagate.set_global_textmap(CompositePropagator([]))
        create_app(_settings())
        assert propagate.get_global_textmap().fields == {"traceparent", "tracestate"}


@pytest.mark.usefixtures("migrated_db")
class TestSampling:
    async def test_ratio_zero_drops_new_traces(self, exporter: InMemorySpanExporter) -> None:
        app = create_app(_settings(ratio=0))
        await _run(app, "GET", "/api/v1/items")
        assert _spans(exporter, app) == []

    async def test_a_sampled_parent_is_honoured_at_ratio_zero(
        self, exporter: InMemorySpanExporter
    ) -> None:
        app = create_app(_settings(ratio=0))
        await _run(
            app, "GET", "/api/v1/items", headers={"traceparent": f"00-{TRACE_ID}-{PARENT_ID}-01"}
        )
        assert {format(s.context.trace_id, "032x") for s in _spans(exporter, app)} == {TRACE_ID}

    async def test_an_unsampled_parent_is_honoured_at_ratio_one(
        self, exporter: InMemorySpanExporter
    ) -> None:
        app = create_app(_settings(ratio=1))
        await _run(
            app, "GET", "/api/v1/items", headers={"traceparent": f"00-{TRACE_ID}-{PARENT_ID}-00"}
        )
        assert _spans(exporter, app) == []


@pytest.mark.usefixtures("migrated_db")
class TestOneTraceId:
    @pytest.mark.parametrize("inbound", [True, False])
    async def test_span_log_and_problem_share_the_trace_id(
        self, exporter: InMemorySpanExporter, captured: LogCaptureHandler, *, inbound: bool
    ) -> None:
        app = create_app(_settings())
        headers = {"traceparent": f"00-{TRACE_ID}-{PARENT_ID}-01"} if inbound else {}
        response = await _run(app, "GET", "/api/v1/no-such-route", headers=headers)
        assert response.status_code == httpx.codes.NOT_FOUND
        server = next(s for s in _spans(exporter, app) if s.kind == SpanKind.SERVER)
        span_trace = format(server.context.trace_id, "032x")
        span_id = format(server.context.span_id, "016x")
        if inbound:
            assert span_trace == TRACE_ID
            assert server.parent is not None
            assert format(server.parent.span_id, "016x") == PARENT_ID
        body = response.json()
        assert body["trace_id"] == span_trace
        request_logs = [
            r
            for r in captured.records
            if r.name.endswith("middleware.logging") and "no-such-route" in r.getMessage()
        ]
        assert request_logs
        assert {r.trace_id for r in request_logs} == {span_trace}  # type: ignore[attr-defined]
        assert {r.span_id for r in request_logs} <= {span_id}  # type: ignore[attr-defined]

    async def test_the_forwarded_traceparent_names_the_active_span(
        self, exporter: InMemorySpanExporter
    ) -> None:
        from your_service_name.backend.logging.correlation import (  # noqa: PLC0415
            outgoing_trace_headers,
        )

        app = create_app(_settings())
        seen: dict[str, str] = {}

        @app.get("/probe-forward")
        async def forward() -> dict[str, str]:
            seen.update(outgoing_trace_headers())
            return {}

        await _run(
            app, "GET", "/probe-forward", headers={"traceparent": f"00-{TRACE_ID}-{PARENT_ID}-01"}
        )
        server = next(s for s in _spans(exporter, app) if s.kind == SpanKind.SERVER)
        assert seen["traceparent"] == f"00-{TRACE_ID}-{format(server.context.span_id, '016x')}-01"


class _Raising(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:  # noqa: ARG002
        msg = "collector down"
        raise RuntimeError(msg)


class _Stalling(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:  # noqa: ARG002
        time.sleep(3)
        return SpanExportResult.SUCCESS


@pytest.mark.usefixtures("migrated_db")
class TestFailingExporter:
    async def test_a_raising_exporter_does_not_fail_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tracing, "build_exporter", lambda _settings: _Raising())
        app = create_app(_settings())
        response = await _run(app, "GET", "/api/v1/items")
        assert response.status_code == httpx.codes.OK
        app.state.tracing.provider.force_flush(timeout_millis=500)  # must not raise

    async def test_a_stalled_exporter_does_not_slow_requests_or_hang_shutdown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tracing, "build_exporter", lambda _settings: _Stalling())
        monkeypatch.setattr(tracing, "SHUTDOWN_TIMEOUT_SECONDS", 0.3)
        app = create_app(_settings())
        started = time.monotonic()
        response = await _run(app, "GET", "/api/v1/items")  # includes the bounded shutdown
        assert response.status_code == httpx.codes.OK
        assert time.monotonic() - started < 2.5  # noqa: PLR2004


def test_the_default_exporter_gets_explicit_values() -> None:
    exporter = tracing.build_exporter(
        Settings(
            TRACING_ENABLED=True,
            OTEL_EXPORTER_OTLP_ENDPOINT=ENDPOINT,
            OTEL_EXPORTER_OTLP_HEADERS=SecretStr("authorization=Bearer fake"),
        )
    )
    client = exporter._client  # type: ignore[attr-defined]
    assert client._endpoint == f"{ENDPOINT}/v1/traces"
    assert client._headers["authorization"] == "Bearer fake"
    assert client._timeout == tracing.EXPORT_TIMEOUT_SECONDS


def test_tracing_off_installs_nothing() -> None:
    app = create_app(Settings())
    assert not hasattr(app.state, "tracing")
