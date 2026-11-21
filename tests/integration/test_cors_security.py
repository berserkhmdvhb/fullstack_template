# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

from typing import Final

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import status

from your_service_name.backend.main import create_app


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_headers_present(api_base: str) -> None:
    """
    Security middleware injects standard headers on responses.
    HSTS is only set for HTTPS requests (tested separately).
    """
    app = create_app()

    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        url: Final[str] = f"{api_base}/health"
        res: httpx.Response = await client.get(url)
        assert res.status_code == status.HTTP_200_OK
        assert res.json() == {"status": "ok"}  # HealthStatus schema.

        # Security middleware headers (always present).
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("Referrer-Policy") == "no-referrer"

        # HSTS is only set for HTTPS requests (see test_security_headers_https).
        assert res.headers.get("Strict-Transport-Security") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_security_headers_https(api_base: str) -> None:
    """
    Security middleware sets HSTS header for HTTPS requests.
    """
    app = create_app()

    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client,
    ):
        url: Final[str] = f"{api_base}/health"
        res: httpx.Response = await client.get(url)
        assert res.status_code == status.HTTP_200_OK

        hsts: str | None = res.headers.get("Strict-Transport-Security")
        assert hsts is not None
        assert "max-age=" in hsts


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cors_origins_and_credentials(
    monkeypatch: pytest.MonkeyPatch,
    api_base: str,
) -> None:
    """
    When CORS_ALLOW_CREDENTIALS=true, '*' must be removed; Starlette reflects
    the explicit Origin from the request.
    """
    monkeypatch.setenv("CORS_ORIGINS", "https://a.test, https://b.test, *")
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")

    # create_app() reads CORS_ORIGINS + CORS_ALLOW_CREDENTIALS from env.
    app = create_app()

    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        preflight_headers: dict[str, str] = {
            "Origin": "https://a.test",
            "Access-Control-Request-Method": "GET",
        }

        res: httpx.Response = await client.options(
            f"{api_base}/health",
            headers=preflight_headers,
        )

        # With credentials=true, "*" is removed; CORS echoes the explicit origin.
        assert res.headers.get("access-control-allow-origin") == "https://a.test"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cors_default_same_origin(
    monkeypatch: pytest.MonkeyPatch,
    api_base: str,
) -> None:
    """
    With ``CORS_ORIGINS`` unset the service is same-origin-restrictive: it does
    NOT reflect an arbitrary Origin, so a cross-origin browser read is denied.

    (The template ships restrictive-by-default rather than reflecting every
    origin; an adopter opts in via ``CORS_ORIGINS``.)
    """
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    app = create_app()

    async with (
        LifespanManager(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        preflight_headers: dict[str, str] = {
            "Origin": "https://evil.test",
            "Access-Control-Request-Method": "GET",
        }

        res: httpx.Response = await client.options(
            f"{api_base}/health",
            headers=preflight_headers,
        )

        # No allow-list configured → no origin is reflected (same-origin only).
        assert res.headers.get("access-control-allow-origin") is None
