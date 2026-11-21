# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Live tests for the full stack exercised through a single public edge.

Covers:
- Health, docs, OpenAPI spec, security headers
- Echo roundtrip, items CRUD
- Against a deployed public reverse-proxy edge (in production, the NGINX
  Ingress Controller) when one is explicitly targeted via PUBLIC_PORT.

These tests need one public port that routes "/", "/api/*", "/docs" and
"/api/healthz". In the CI ``live`` job (``entrypoint.sh --mode all``) that port is the
Next.js frontend, which serves the pages and proxies the API and the docs; the job
sets PUBLIC_PORT to it. nginx there is static-only (see ``test_header_set.py``).
The suite is skipped, with that reason, unless PUBLIC_PORT is set (see the
``_require_public_edge`` fixture); set it to run against a deployed edge.
"""

from __future__ import annotations

import os
from typing import Final

import httpx
import pytest
from fastapi import status


def _public_url() -> str:
    """Compute the public edge URL from environment.

    Requires an explicitly-configured edge (PUBLIC_PORT); there is no default —
    the retired HAProxy port (:8400) must never be assumed. The suite is
    skipped when no edge is configured, so this is only reached with it set.
    """
    host: Final[str] = os.getenv("PUBLIC_HOST", "127.0.0.1")
    port: Final[str] = os.environ["PUBLIC_PORT"]
    return f"http://{host}:{port}"


@pytest.fixture(autouse=True)
def _require_public_edge() -> None:
    """Skip this suite unless a public edge is explicitly targeted.

    Edge routing is a deployment-infrastructure concern (the NGINX Ingress
    Controller in production); the hermetic CI job does not provision one. Skip
    rather than fail against a non-existent port, and run when an edge is
    configured via PUBLIC_PORT (optionally PUBLIC_HOST).
    """
    if not os.getenv("PUBLIC_PORT"):
        pytest.skip(
            "no public edge configured: set PUBLIC_PORT (and optionally "
            "PUBLIC_HOST) to target a deployed reverse-proxy edge"
        )


def _backend_url() -> str:
    """Compute backend base URL from environment."""
    host: Final[str] = os.getenv("BACKEND_HOST", "127.0.0.1")
    port: Final[str] = os.getenv("BACKEND_PORT", "8000")
    return f"http://{host}:{port}"


def _api_base() -> str:
    """Compute versioned API base (e.g., '/api/v1') from environment."""
    api_version: Final[str] = os.getenv("API_VERSION", "v1")
    return f"/api/{api_version}"


def _client(base_url: str, timeout: float = 10.0) -> httpx.AsyncClient:
    """Build an httpx client with ambient proxy config disabled.

    CI runners (especially self-hosted ones) frequently export
    HTTP_PROXY/HTTPS_PROXY/ALL_PROXY. httpx honours those by default and would
    route loopback (127.0.0.1) traffic through the proxy, failing every request
    to the locally-started services while the no-proxy waiter still succeeds.
    ``trust_env=False`` ignores env/netrc proxies — the httpx parallel to
    wait_for_service.py's ``_NO_PROXY_OPENER``.
    """
    return httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False)


# ---------------------------------------------------------------------------
# Tests: Health endpoints
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_public_health() -> None:
    """GET /health via the public edge should return ok."""
    public_url = _public_url()
    api_base = _api_base()

    async with _client(base_url=public_url, timeout=10.0) as client:
        res = await client.get(f"{api_base}/health")

        assert res.status_code == status.HTTP_200_OK
        assert res.json() == {"status": "ok"}


@pytest.mark.live
@pytest.mark.asyncio
async def test_public_healthz() -> None:
    """GET /api/<version>/healthz (process-only liveness) is served by every topology."""
    public_url = _public_url()
    api_base = _api_base()

    async with _client(base_url=public_url, timeout=10.0) as client:
        res = await client.get(f"{api_base}/healthz")

        assert res.status_code == status.HTTP_200_OK
        assert res.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# Tests: API docs
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_public_docs_accessible() -> None:
    """GET /docs via the public edge should be accessible."""
    public_url = _public_url()

    async with _client(base_url=public_url, timeout=10.0) as client:
        res = await client.get("/docs")

        assert res.status_code == status.HTTP_200_OK


@pytest.mark.live
@pytest.mark.asyncio
async def test_public_openapi_spec() -> None:
    """GET /openapi.json via the public edge should return valid spec."""
    public_url = _public_url()

    async with _client(base_url=public_url, timeout=10.0) as client:
        res = await client.get("/openapi.json")

        assert res.status_code == status.HTTP_200_OK
        data = res.json()
        assert "openapi" in data
        assert "paths" in data


# ---------------------------------------------------------------------------
# Tests: Security headers
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_public_security_headers() -> None:
    """Responses via the public edge should have security headers."""
    public_url = _public_url()
    api_base = _api_base()

    async with _client(base_url=public_url, timeout=10.0) as client:
        res = await client.get(f"{api_base}/health")

        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("Referrer-Policy") == "no-referrer"


# ---------------------------------------------------------------------------
# Tests: Echo roundtrip via the public edge
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_public_echo_roundtrip() -> None:
    """POST /echo and GET /echo/history via the public edge."""
    public_url = _public_url()
    api_base = _api_base()

    async with _client(base_url=public_url, timeout=10.0) as client:
        # Create echo
        create_res = await client.post(
            f"{api_base}/echo",
            json={"text": "hello via proxy"},
        )
        assert create_res.status_code == status.HTTP_201_CREATED
        assert create_res.json()["text"] == "hello via proxy"

        # Get history
        hist_res = await client.get(f"{api_base}/echo/history")
        assert hist_res.status_code == status.HTTP_200_OK
        items = hist_res.json()
        assert any(item["text"] == "hello via proxy" for item in items)


# ---------------------------------------------------------------------------
# Tests: Items CRUD via the public edge
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_public_items_crud() -> None:
    """Full items CRUD via the public edge."""
    public_url = _public_url()
    api_base = _api_base()

    async with _client(base_url=public_url, timeout=10.0) as client:
        # Create
        create_res = await client.post(
            f"{api_base}/items",
            json={"name": "Proxy Test Item", "description": "Via edge"},
        )
        assert create_res.status_code == status.HTTP_201_CREATED
        item_id = create_res.json()["id"]

        # Read
        get_res = await client.get(f"{api_base}/items/{item_id}")
        assert get_res.status_code == status.HTTP_200_OK
        assert get_res.json()["name"] == "Proxy Test Item"

        # Update
        update_res = await client.patch(
            f"{api_base}/items/{item_id}",
            json={"name": "Updated via Proxy"},
        )
        assert update_res.status_code == status.HTTP_200_OK
        assert update_res.json()["name"] == "Updated via Proxy"

        # Delete
        delete_res = await client.delete(f"{api_base}/items/{item_id}")
        assert delete_res.status_code == status.HTTP_204_NO_CONTENT


# ---------------------------------------------------------------------------
# Tests: Frontend accessible
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_public_frontend_loads() -> None:
    """GET / via the public edge should return frontend HTML."""
    public_url = _public_url()

    async with _client(base_url=public_url, timeout=10.0) as client:
        res = await client.get("/")

        assert res.status_code == status.HTTP_200_OK
        # Should contain some HTML
        assert "<!DOCTYPE html>" in res.text or "<html" in res.text.lower()
