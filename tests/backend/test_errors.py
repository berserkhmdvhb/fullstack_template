# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Every error is an RFC 9457 problem body, and none of them leaks internals."""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.middleware import Middleware

from your_service_name.backend.auth.dependencies import NotAuthenticatedError
from your_service_name.backend.logging.correlation import RequestIdFilter
from your_service_name.backend.main import create_app
from your_service_name.backend.middleware.correlation import CorrelationIdMiddleware
from your_service_name.backend.settings import Settings

if TYPE_CHECKING:
    from starlette.types import Receive, Scope, Send

PROBLEM = "application/problem+json"
SECRET = "hunter2-Sensitive-Value"  # noqa: S105 — a canary: it must never come back in a body
LEAKS = ("Traceback", ".py", "RuntimeError", 'File "', SECRET)
REQUIRED = {"type", "title", "status", "detail", "instance", "request_id"}


class _Body(BaseModel):
    count: int
    token: str


class _Boom:
    """A middleware (inside the correlation one) that raises while handling a request."""

    def __init__(self, app: Any) -> None:  # noqa: ANN401
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == "/mw-boom":
            msg = f"middleware broke with {SECRET}"
            raise RuntimeError(msg)
        await self.app(scope, receive, send)


def _build(settings: Settings | None = None) -> FastAPI:
    app = create_app(settings or Settings())

    @app.get("/t/bad")
    def bad() -> None:
        raise HTTPException(status_code=400, detail="That is not valid.")

    @app.get("/t/unauth")
    def unauth() -> None:
        msg = "Missing credentials."
        raise NotAuthenticatedError(msg)

    @app.get("/t/forbidden")
    def forbidden() -> None:
        raise HTTPException(status_code=403, detail="Not allowed.")

    @app.get("/t/boom")
    def boom() -> None:
        msg = f"database password is {SECRET}"
        raise RuntimeError(msg)

    @app.get("/t/five")
    def five() -> None:
        raise HTTPException(status_code=500, detail=f"leaky {SECRET}")

    @app.post("/t/body")
    def body(payload: _Body) -> None:
        del payload

    classes = [m.cls for m in app.user_middleware]
    where = classes.index(CorrelationIdMiddleware)  # type: ignore[arg-type]
    app.user_middleware.insert(where + 1, Middleware(_Boom))
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build(), raise_server_exceptions=False)


def _assert_problem(res: Any, status: int, path: str) -> dict[str, Any]:  # noqa: ANN401
    assert res.status_code == status
    assert res.headers["content-type"].split(";")[0] == PROBLEM
    body = res.json()
    assert body.keys() >= REQUIRED
    assert body["type"] == "about:blank"
    assert body["status"] == status
    assert body["title"]
    assert body["instance"] == path
    assert body["request_id"]
    for leak in LEAKS:
        assert leak not in res.text
    return body  # type: ignore[no-any-return]


def test_400(client: TestClient) -> None:
    body = _assert_problem(client.get("/t/bad?token=abc"), 400, "/t/bad")
    assert body["detail"] == "That is not valid."
    assert "abc" not in str(body)


def test_401_keeps_www_authenticate(client: TestClient) -> None:
    res = client.get("/t/unauth")
    body = _assert_problem(res, 401, "/t/unauth")
    assert body["detail"] == "Missing credentials."
    assert res.headers["www-authenticate"] == "Bearer"


def test_403(client: TestClient) -> None:
    _assert_problem(client.get("/t/forbidden"), 403, "/t/forbidden")


def test_404_unknown_route_hides_the_query(client: TestClient) -> None:
    res = client.get("/nope?access_token=abc")
    body = _assert_problem(res, 404, "/nope")
    assert body["title"] == "Not Found"
    assert "abc" not in res.text


def test_404_from_a_route() -> None:
    with TestClient(_build()) as client:  # lifespan on: the items route needs the database
        res = client.get("/api/v1/items/00000000-0000-0000-0000-000000000000")
    body = _assert_problem(res, 404, "/api/v1/items/00000000-0000-0000-0000-000000000000")
    assert "not found" in body["detail"].lower()


def test_405_keeps_allow(client: TestClient) -> None:
    res = client.post("/t/bad")
    _assert_problem(res, 405, "/t/bad")
    assert "GET" in res.headers["allow"]


def test_422_lists_fields_without_values(client: TestClient) -> None:
    res = client.post("/t/body?x=1", json={"count": "many", "token": SECRET, "extra": SECRET})
    body = _assert_problem(res, 422, "/t/body")
    assert body["errors"], "field errors expected"
    for error in body["errors"]:
        assert set(error) == {"loc", "msg", "type"}
    assert ["body", "count"] in [e["loc"] for e in body["errors"]]


def test_429_keeps_retry_after() -> None:
    client = TestClient(
        _build(Settings(RATE_LIMIT_DEFAULT="1/minute")), raise_server_exceptions=False
    )
    assert client.get("/t/bad").status_code == HTTPStatus.BAD_REQUEST
    res = client.get("/t/bad?q=1")
    _assert_problem(res, 429, "/t/bad")
    assert res.headers["retry-after"] == "60"


def test_500_from_a_route_is_generic(client: TestClient) -> None:
    body = _assert_problem(client.get("/t/boom"), 500, "/t/boom")
    assert body["detail"] == "The server could not process the request."


def test_500_http_exception_detail_is_not_echoed(client: TestClient) -> None:
    _assert_problem(client.get("/t/five"), 500, "/t/five")


def test_500_from_inside_a_middleware_carries_the_request_id(client: TestClient) -> None:
    res = client.get("/mw-boom?q=1", headers={"X-Request-Id": "abc-123"})
    body = _assert_problem(res, 500, "/mw-boom")
    assert body["request_id"] == "abc-123"


def test_500_is_the_same_whatever_the_debug_setting(client: TestClient) -> None:
    app = _build()
    assert app.debug is False
    _assert_problem(client.get("/t/boom"), 500, "/t/boom")


def test_unhandled_exception_is_logged_with_the_request_id(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("ERROR"):
        client.get("/mw-boom", headers={"X-Request-Id": "log-me"})
    record = next(r for r in caplog.records if r.getMessage() == "Unhandled exception")
    assert record.exc_info is not None


TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def test_problem_carries_the_trace_id_when_the_caller_sent_one(client: TestClient) -> None:
    body = _assert_problem(client.get("/nope", headers={"traceparent": TRACEPARENT}), 404, "/nope")
    assert body["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert "trace_id" not in client.get("/nope").json()


def test_500_from_inside_a_middleware_keeps_the_ids_and_echoes_the_request_id(
    client: TestClient,
) -> None:
    res = client.get("/mw-boom", headers={"X-Request-Id": "r-1", "traceparent": TRACEPARENT})
    body = _assert_problem(res, 500, "/mw-boom")
    assert (body["request_id"], body["trace_id"]) == ("r-1", "4bf92f3577b34da6a3ce929d0e0e4736")
    assert res.headers["x-request-id"] == "r-1"


def test_the_500_log_line_carries_both_ids(client: TestClient) -> None:
    seen: list[tuple[str, str | None]] = []

    class _Grab(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            RequestIdFilter().filter(record)
            seen.append((getattr(record, "request_id", ""), getattr(record, "trace_id", None)))
            return True

    log = logging.getLogger("your_service_name.backend.errors")
    flt = _Grab()
    log.addFilter(flt)
    try:
        client.get("/mw-boom", headers={"X-Request-Id": "r-2", "traceparent": TRACEPARENT})
    finally:
        log.removeFilter(flt)
    assert seen == [("r-2", "4bf92f3577b34da6a3ce929d0e0e4736")]
