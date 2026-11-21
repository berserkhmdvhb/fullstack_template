# your-service-name — adaptable service template (see wiki/getting-started.md)
"""End to end: the resolved client IP is what the rate limiter keys on and the logs carry."""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import status
from pydantic import SecretStr

from your_service_name.backend.main import create_app
from your_service_name.backend.settings import Settings

ECHO_PATH = "/api/v1/echo"
GATEWAY = "10.1.2.3"  # inside TRUSTED_PROXY_CIDRS
STRANGER = "198.51.100.9"  # outside it
CLIENT_A, CLIENT_B, FORGED = "203.0.113.5", "203.0.113.6", "192.0.2.66"
LOG_NAME = "your_service_name.backend.middleware.logging"

Connect = Callable[[str], AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
async def connect(tmp_path: Path) -> AsyncGenerator[Connect]:
    """A client factory: ``connect(peer)`` talks to one app as if from TCP peer ``peer``."""
    settings = Settings(
        RATE_LIMIT_DEFAULT="2/minute",
        LOG_REQUESTS=True,
        TRUSTED_PROXY_CIDRS=[ipaddress.ip_network("10.0.0.0/8")],
        TEST_DATABASE_URL=SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"),
    )
    app = create_app(settings)

    @asynccontextmanager
    async def client(peer: str) -> AsyncGenerator[httpx.AsyncClient]:
        transport = httpx.ASGITransport(app=app, client=(peer, 4242))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    async with LifespanManager(app):
        yield client


async def _echo(c: httpx.AsyncClient, xff: str | None = None) -> int:
    headers = {"X-Forwarded-For": xff} if xff else {}
    return (await c.post(ECHO_PATH, json={"text": "hi"}, headers=headers)).status_code


def test_the_client_ip_middleware_is_outermost() -> None:
    """Resolved first, so correlation, logging, the limiter and CORS all see one address."""
    names = [getattr(m.cls, "__name__", "") for m in create_app(Settings()).user_middleware]
    names = [n for n in names if n != "PrometheusInstrumentatorMiddleware"]  # metrics only
    ours = ["ClientIpMiddleware", "CorrelationIdMiddleware", "RequestLoggingMiddleware"]
    assert names[0] == "ClientIpMiddleware"
    assert [n for n in names if n in ours] == ours
    assert names.index("ClientIpMiddleware") < names.index("RateLimitMiddleware")
    assert names.index("ClientIpMiddleware") < names.index("CORSMiddleware")


@pytest.mark.integration
async def test_each_client_behind_a_trusted_gateway_has_its_own_bucket(connect: Connect) -> None:
    async with connect(GATEWAY) as c:
        assert [await _echo(c, CLIENT_A) for _ in range(3)] == [201, 201, 429]
        # another client through the same gateway is not throttled by A's requests
        assert await _echo(c, CLIENT_B) == status.HTTP_201_CREATED


@pytest.mark.integration
async def test_forged_headers_from_an_untrusted_peer_share_the_peers_bucket(
    connect: Connect,
) -> None:
    """Rotating a forged X-Forwarded-For does not escape the limit; the peer is the key."""
    async with connect(STRANGER) as c:
        codes = [await _echo(c, xff) for xff in (CLIENT_A, CLIENT_B, FORGED)]
    assert codes == [201, 201, 429]


@pytest.mark.integration
async def test_the_request_log_carries_the_resolved_client(
    connect: Connect, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOG_NAME)
    async with connect(GATEWAY) as c:
        await _echo(c, f"{FORGED}, {CLIENT_A}")
    assert f"client={CLIENT_A}" in caplog.text
    assert FORGED not in caplog.text


@pytest.mark.integration
async def test_with_an_untrusted_peer_the_log_carries_the_peer(
    connect: Connect, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=LOG_NAME)
    async with connect(STRANGER) as c:
        await _echo(c, FORGED)
    assert f"client={STRANGER}" in caplog.text
    assert FORGED not in caplog.text
