# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Body-size limit and trusted hosts: problem bodies, request ids, probe exemptions."""

from __future__ import annotations

from collections.abc import Iterator
from http import HTTPStatus
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from your_service_name.backend import paths
from your_service_name.backend.main import create_app
from your_service_name.backend.middleware.request_limits import (
    RequestSizeLimitMiddleware,
    TrustedHostMiddleware,
)
from your_service_name.backend.settings import Settings
from your_service_name.utils.versioning_api import DEFAULT_API_VERSION

PROBLEM = "application/problem+json"
LIMIT = 100


class _Model(BaseModel):
    count: int = 0
    pad: str = ""


def _app(**settings: Any) -> FastAPI:  # noqa: ANN401
    app = create_app(Settings(MAX_REQUEST_BODY_BYTES=LIMIT, **settings))

    @app.post("/t/echo")
    async def echo(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    @app.post("/t/model")
    async def model(payload: _Model) -> dict[str, int]:
        return {"count": payload.count}

    return app


def _problem(res: Any, status: int, path: str) -> dict[str, Any]:  # noqa: ANN401
    assert res.status_code == status
    assert res.headers["content-type"].split(";")[0] == PROBLEM
    body = res.json()
    assert (body["status"], body["instance"]) == (status, path)
    assert body["title"] == HTTPStatus(status).phrase
    assert body["request_id"] == res.headers["x-request-id"]
    for leak in ("Traceback", ".py"):
        assert leak not in res.text
    return body  # type: ignore[no-any-return]


# --- body size -----------------------------------------------------------------------------------


def test_a_body_at_the_limit_is_accepted() -> None:
    res = TestClient(_app()).post("/t/echo", content=b"x" * LIMIT)
    assert res.json() == {"bytes": LIMIT}


def test_content_length_over_the_limit_is_a_413_problem() -> None:
    res = TestClient(_app()).post("/t/echo?q=secret", content=b"x" * (LIMIT + 1))
    _problem(res, 413, "/t/echo")
    assert "secret" not in res.text


def test_the_413_echoes_a_caller_request_id() -> None:
    res = TestClient(_app()).post(
        "/t/echo", content=b"x" * (LIMIT + 1), headers={"X-Request-Id": "abc-1"}
    )
    assert _problem(res, 413, "/t/echo")["request_id"] == "abc-1"


def _chunks(total: int, size: int = 30) -> Iterator[bytes]:
    for _ in range(0, total, size):
        yield b"x" * size


def test_a_chunked_body_without_a_length_is_cut_off_with_a_413() -> None:
    res = TestClient(_app()).post("/t/echo", content=_chunks(LIMIT * 3))
    assert "content-length" not in res.request.headers
    _problem(res, 413, "/t/echo")


def test_a_chunked_json_body_parsed_by_fastapi_is_a_413_not_a_400() -> None:
    """FastAPI turns a failed body read into a 400; the limit's 413 must win."""

    def json_chunks() -> Iterator[bytes]:
        yield b'{"count": 1, "pad": "'
        yield from _chunks(LIMIT * 3)
        yield b'"}'

    res = TestClient(_app()).post(
        "/t/model", content=json_chunks(), headers={"Content-Type": "application/json"}
    )
    _problem(res, 413, "/t/model")


def test_a_chunked_body_within_the_limit_is_accepted() -> None:
    assert TestClient(_app()).post("/t/echo", content=_chunks(60)).status_code == 200  # noqa: PLR2004


def test_the_streaming_413_is_a_problem_even_if_the_app_does_not_handle_it() -> None:
    """The middleware answers itself when nothing inside turned the exception into a response."""

    async def reader(_scope: Any, receive: Any, _send: Any) -> None:  # noqa: ANN401
        while (await receive()).get("more_body"):
            pass

    app = RequestSizeLimitMiddleware(reader, max_bytes=10)
    wrapper = FastAPI()
    wrapper.mount("/", app)
    res = TestClient(wrapper).post("/x", content=_chunks(100))
    assert res.status_code == 413  # noqa: PLR2004
    assert res.headers["content-type"].split(";")[0] == PROBLEM


# --- trusted hosts -------------------------------------------------------------------------------


def _hosts(*hosts: str) -> TestClient:
    return TestClient(_app(ALLOWED_HOSTS=",".join(hosts)))


def test_a_listed_host_passes_with_or_without_a_port() -> None:
    client = _hosts("api.example.com")
    for host in ("api.example.com", "API.example.com", "api.example.com:8000"):
        assert client.post("/t/echo", content=b"", headers={"Host": host}).status_code == 200  # noqa: PLR2004


def test_an_ipv6_literal_host_with_a_port_matches_without_it() -> None:
    client = _hosts("[::1]")
    assert client.get("/t/none", headers={"Host": "[::1]:8000"}).status_code == 404  # noqa: PLR2004
    assert client.get("/t/none", headers={"Host": "[::2]:8000"}).status_code == 400  # noqa: PLR2004


@pytest.mark.parametrize(
    "host",
    [
        "[::1]junk",
        "[::1",
        "[zz::1]",
        "evil.test#.example.com",
        "evil.test/.example.com",
        "a b.example.com",
        "x.example.com:port",
        "x.example.com:",
        "-x.example.com",
        "x..example.com",
        "x.example.com?",
        "us_er.example.com",
        "x.example.com:99999999",
        "x.example.com\x00",
    ],
)
def test_a_malformed_host_is_a_400_even_when_it_ends_like_an_allowed_pattern(host: str) -> None:
    res = _hosts("*.example.com", "[::1]").get("/t/none", headers={"Host": host})
    assert res.status_code == 400  # noqa: PLR2004


@pytest.mark.parametrize("host", ["a.example.com:8000", "a-b.c.example.com", "A.EXAMPLE.com:1"])
def test_a_well_formed_host_matching_a_wildcard_is_accepted(host: str) -> None:
    res = _hosts("*.example.com").get("/t/none", headers={"Host": host})
    assert res.status_code == 404  # noqa: PLR2004


def test_an_ipv4_literal_host_is_matched_exactly() -> None:
    client = _hosts("10.0.0.7")
    assert client.get("/t/none", headers={"Host": "10.0.0.7:80"}).status_code == 404  # noqa: PLR2004
    assert client.get("/t/none", headers={"Host": "10.0.0.8"}).status_code == 400  # noqa: PLR2004


def test_an_unlisted_host_is_a_400_problem() -> None:
    client = _hosts("api.example.com")
    res = client.get("/api/v1/items?token=zq-tok-zq", headers={"Host": "evil.test"})
    body = _problem(res, 400, "/api/v1/items")
    assert "evil.test" not in res.text
    assert "zq-tok-zq" not in res.text
    assert body["detail"]


@pytest.mark.parametrize("host", ["evil.test", "example.com", "x.example.com.evil.test", ""])
def test_wildcards_match_subdomains_only(host: str) -> None:
    res = _hosts("*.example.com").get("/t/none", headers={"Host": host})
    assert res.status_code == 400  # noqa: PLR2004


def test_a_wildcard_pattern_accepts_a_subdomain() -> None:
    res = _hosts("*.example.com").get("/t/none", headers={"Host": "a.example.com"})
    assert res.status_code == 404  # noqa: PLR2004


@pytest.mark.parametrize("path", ["/api/v1/health", "/api/v1/healthz"])
def test_probe_paths_accept_any_host(path: str) -> None:
    with TestClient(_app(ALLOWED_HOSTS="api.example.com")) as client:
        res = client.get(path, headers={"Host": "10.42.0.17:8000"})
    assert res.status_code != 400  # noqa: PLR2004


def test_the_default_allows_any_host() -> None:
    assert TestClient(_app()).get("/t/none", headers={"Host": "anything.test"}).status_code == 404  # noqa: PLR2004


def test_the_exempt_paths_are_the_rate_limiters() -> None:
    inner = TrustedHostMiddleware(lambda *_: None, allowed_hosts=["api.example.com"])  # type: ignore[arg-type]
    assert inner._exempt == paths.exempt_paths(DEFAULT_API_VERSION)


def test_the_limits_sit_just_inside_client_ip_and_correlation() -> None:
    """Outermost first; the refusals carry the request id, and nothing but those two is outside."""
    names = [getattr(m.cls, "__name__", "") for m in create_app(Settings()).user_middleware]
    names.remove("PrometheusInstrumentatorMiddleware")  # added last, so outermost; see the docs
    assert names[:4] == [
        "ClientIpMiddleware",
        "CorrelationIdMiddleware",
        "RequestSizeLimitMiddleware",
        "TrustedHostMiddleware",
    ]


@pytest.mark.parametrize(
    "path", ["/api/v1/items/metrics", "/x/health", "/health", "/api/v1/healthz/x"]
)
def test_a_path_that_merely_ends_like_a_probe_is_not_exempt(path: str) -> None:
    res = _hosts("api.example.com").get(path, headers={"Host": "10.42.0.17"})
    assert res.status_code == 400  # noqa: PLR2004
