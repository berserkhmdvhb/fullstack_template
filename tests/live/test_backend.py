# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

import os
from typing import Final

import httpx
import pytest
from fastapi import status


def _api_base() -> str:
    """Compute versioned API base (e.g., '/api/v1') from environment."""
    api_version: Final[str] = os.getenv("API_VERSION", "v1")
    return f"/api/{api_version}"


def _client(base_url: str, timeout: float = 5.0) -> httpx.AsyncClient:
    """Build an httpx client with ambient proxy config disabled.

    CI runners (especially self-hosted ones) frequently export
    HTTP_PROXY/HTTPS_PROXY/ALL_PROXY. httpx honours those by default and would
    route loopback (127.0.0.1) traffic through the proxy, failing every request
    to the locally-started services while the no-proxy waiter still succeeds.
    ``trust_env=False`` ignores env/netrc proxies — the httpx parallel to
    wait_for_service.py's ``_NO_PROXY_OPENER``.
    """
    return httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False)


@pytest.mark.live
@pytest.mark.asyncio
async def test_backend_health() -> None:
    """Validate the backend health endpoint directly (on its own port)."""
    host: Final[str] = os.getenv("BACKEND_HOST", "127.0.0.1")
    port: Final[str] = os.getenv("BACKEND_PORT", "8000")
    base_url: Final[str] = f"http://{host}:{port}"
    api_base: Final[str] = _api_base()

    async with _client(base_url=base_url, timeout=5.0) as client:
        res = await client.get(f"{api_base}/health")
        assert res.status_code == status.HTTP_200_OK
        assert res.json() == {"status": "ok"}


@pytest.mark.live
@pytest.mark.asyncio
async def test_backend_docs_accessible() -> None:
    """Validate FastAPI /docs and /openapi.json are reachable on the backend."""
    host: Final[str] = os.getenv("BACKEND_HOST", "127.0.0.1")
    port: Final[str] = os.getenv("BACKEND_PORT", "8000")
    base_url: Final[str] = f"http://{host}:{port}"

    async with _client(base_url=base_url, timeout=5.0) as client:
        docs = await client.get("/docs")
        assert docs.status_code == status.HTTP_200_OK

        openapi = await client.get("/openapi.json")
        assert openapi.status_code == status.HTTP_200_OK


@pytest.mark.live
@pytest.mark.asyncio
async def test_backend_security_headers() -> None:
    """Validate backend security headers are present on responses."""
    host: Final[str] = os.getenv("BACKEND_HOST", "127.0.0.1")
    port: Final[str] = os.getenv("BACKEND_PORT", "8000")
    base_url: Final[str] = f"http://{host}:{port}"
    api_base: Final[str] = _api_base()

    async with _client(base_url=base_url, timeout=5.0) as client:
        res = await client.get(f"{api_base}/health")
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("Referrer-Policy") == "no-referrer"


@pytest.mark.live
@pytest.mark.asyncio
async def test_backend_echo_roundtrip() -> None:
    """Validate POST /echo and GET /echo/history on the standalone backend."""
    host: Final[str] = os.getenv("BACKEND_HOST", "127.0.0.1")
    port: Final[str] = os.getenv("BACKEND_PORT", "8000")
    base_url: Final[str] = f"http://{host}:{port}"
    api_base: Final[str] = _api_base()

    async with _client(base_url=base_url, timeout=5.0) as client:
        payload = {"text": "hello backend"}
        created = await client.post(f"{api_base}/echo", json=payload)
        assert created.status_code == status.HTTP_201_CREATED
        assert created.headers.get("Location") == f"{api_base}/echo/history"

        body = created.json()
        assert body["text"] == "hello backend"
        assert body["length"] == len("hello backend")

        hist = await client.get(f"{api_base}/echo/history")
        assert hist.status_code == status.HTTP_200_OK
        items = hist.json()
        assert any(it["text"] == "hello backend" for it in items)
