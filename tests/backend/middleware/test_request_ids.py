# your-service-name — adaptable service template (see wiki/getting-started.md)
"""X-Request-Id validation, traceparent parsing and propagation into the logs."""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from your_service_name.backend.logging.correlation import (
    RequestIdFilter,
    get_trace_id,
    outgoing_trace_headers,
    parse_traceparent,
    reset_trace_context,
    set_trace_context,
)
from your_service_name.backend.logging.json_formatter import JsonLogFormatter
from your_service_name.backend.middleware.correlation import CorrelationIdMiddleware

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT = "00f067aa0ba902b7"
VALID = f"00-{TRACE}-{PARENT}-01"
HEX32 = 32


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/probe")
    def probe() -> dict[str, Any]:
        logging.getLogger("probe").warning("inside the request")
        return {"trace_id": get_trace_id(), "out": outgoing_trace_headers()}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app())


# --- X-Request-Id ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["a" * 65, "has space", "semi;colon", "line\r\nbreak", "tab\there", ""],
)
def test_invalid_request_id_is_replaced(client: TestClient, value: str) -> None:
    res = client.get("/probe", headers={"X-Request-Id": value})
    echoed = res.headers["x-request-id"]
    assert echoed != value
    assert len(echoed) == HEX32
    assert value not in res.text or not value


def test_non_ascii_bytes_are_replaced(client: TestClient) -> None:
    res = client.get("/probe", headers={"X-Request-Id": b"caf\xe9"})
    assert len(res.headers["x-request-id"]) == HEX32


def test_crlf_cannot_reach_the_response_headers(client: TestClient) -> None:
    res = client.get("/probe", headers={"X-Request-Id": "abc\r\nSet-Cookie: x=1"})
    assert "set-cookie" not in res.headers
    assert "\n" not in res.headers["x-request-id"]


@pytest.mark.parametrize("value", ["a" * 64, "req-1", "A.b_c-9", "x"])
def test_valid_request_id_is_kept_and_echoed(client: TestClient, value: str) -> None:
    assert client.get("/probe", headers={"X-Request-Id": value}).headers["x-request-id"] == value


def test_absent_request_id_is_generated(client: TestClient) -> None:
    first = client.get("/probe").headers["x-request-id"]
    assert len(first) == HEX32
    assert first != client.get("/probe").headers["x-request-id"]


def test_rejected_value_is_never_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client.get("/probe", headers={"X-Request-Id": "evil;value"})
    assert "evil" not in caplog.text
    assert any("replaced" in r.getMessage() for r in caplog.records)


# --- traceparent -------------------------------------------------------------------------------


def test_parse_valid() -> None:
    ctx = parse_traceparent(VALID)
    assert ctx is not None
    assert (ctx.trace_id, ctx.parent_id, ctx.flags) == (TRACE, PARENT, "01")


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "garbage",
        f"01-{TRACE}-{PARENT}-01",  # version other than 00
        f"ff-{TRACE}-{PARENT}-01",
        f"00-{TRACE.upper()}-{PARENT}-01",  # lowercase only
        f"00-{'0' * 32}-{PARENT}-01",  # all-zero trace id
        f"00-{TRACE}-{'0' * 16}-01",  # all-zero parent id
        f"00-{TRACE[:-1]}-{PARENT}-01",  # short trace id
        f"00-{TRACE}-{PARENT[:-1]}-01",
        f"00-{TRACE}-{PARENT}-1",  # short flags
        f"00-{TRACE}-{PARENT}-01-extra",  # version 00 has no more fields
        f"00-{TRACE}-{PARENT}-01\n",
        f"00-{TRACE}-{PARENT}-0g",
    ],
)
def test_parse_invalid(value: str | None) -> None:
    assert parse_traceparent(value) is None


def test_trace_id_reaches_the_handler_and_is_forwarded(client: TestClient) -> None:
    body = client.get("/probe", headers={"traceparent": VALID}).json()
    assert body["trace_id"] == TRACE
    assert body["out"] == {"traceparent": VALID}


def test_invalid_or_absent_traceparent_is_ignored_not_an_error(client: TestClient) -> None:
    for headers in ({}, {"traceparent": "nope"}):
        res = client.get("/probe", headers=headers)
        assert res.status_code == 200  # noqa: PLR2004
        assert res.json() == {"trace_id": None, "out": {}}


def test_a_repeated_traceparent_header_is_ignored(client: TestClient) -> None:
    res = client.get("/probe", headers=[("traceparent", VALID), ("traceparent", VALID)])
    assert res.json()["trace_id"] is None


def test_trace_context_does_not_leak_between_requests(client: TestClient) -> None:
    client.get("/probe", headers={"traceparent": VALID})
    assert client.get("/probe").json()["trace_id"] is None
    assert get_trace_id() is None


def _logged(formatter: logging.Formatter) -> str:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())
    log = logging.getLogger("probe")
    log.addHandler(handler)
    try:
        log.warning("hello")
    finally:
        log.removeHandler(handler)
    return stream.getvalue()


def test_json_log_line_carries_trace_id_only_when_set() -> None:
    assert "trace_id" not in json.loads(_logged(JsonLogFormatter()))
    ctx = parse_traceparent(VALID)
    token = set_trace_context(ctx)
    try:
        assert json.loads(_logged(JsonLogFormatter()))["trace_id"] == TRACE
    finally:
        reset_trace_context(token)


def test_text_log_line_carries_trace_id_only_when_set() -> None:
    fmt = logging.Formatter("[request_id=%(request_id)s%(trace_part)s]: %(message)s")
    assert "trace_id" not in _logged(fmt)
    token = set_trace_context(parse_traceparent(VALID))
    try:
        assert f"trace_id={TRACE}" in _logged(fmt)
    finally:
        reset_trace_context(token)


def test_logs_inside_a_request_carry_the_trace_id() -> None:
    seen: list[str | None] = []

    class _Grab(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            RequestIdFilter().filter(record)
            seen.append(getattr(record, "trace_id", None))
            return True

    log = logging.getLogger("probe")
    flt = _Grab()
    log.addFilter(flt)
    try:
        TestClient(_app()).get("/probe", headers={"traceparent": VALID})
    finally:
        log.removeFilter(flt)
    assert seen == [TRACE]


def test_outgoing_headers_are_empty_outside_a_trace() -> None:
    assert outgoing_trace_headers() == {}
