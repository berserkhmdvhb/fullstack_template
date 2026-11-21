# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Live tests for the full stack exercised through a single public edge.

Covers:
- Health, docs, OpenAPI spec, security headers
- Echo roundtrip, items CRUD
- Against a deployed public reverse-proxy edge (in production, the NGINX
  Ingress Controller) when one is explicitly targeted via PUBLIC_PORT.

These tests require a public edge that routes "/", "/api/*", "/docs" and
"/healthz" through a single port. The hermetic CI ``live`` job stands up only
the standalone services — nginx is static-only (no /api proxying) and Next.js
is reached directly — so no such edge exists there. The suite is skipped unless
PUBLIC_PORT is set (see the ``_require_public_edge`` fixture); set it to run
against a deployed edge.
"""

from __future__ import annotations

import os
import re
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
def _require_public_edge(request: pytest.FixtureRequest) -> None:
    """Skip this suite unless a public edge is explicitly targeted.

    Edge routing is a deployment-infrastructure concern (the NGINX Ingress
    Controller in production); the hermetic CI job does not provision one. Skip
    rather than fail against a non-existent port, and run when an edge is
    configured via PUBLIC_PORT (optionally PUBLIC_HOST).
    """
    if request.cls is not None and request.cls.__name__ == "TestFinalHeaderSet":
        return  # targets the frontend and nginx ports of the hermetic stack directly
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
    """GET /healthz via the public edge should return ok."""
    public_url = _public_url()

    async with _client(base_url=public_url, timeout=10.0) as client:
        res = await client.get("/healthz")

        assert res.status_code == status.HTTP_200_OK
        assert "200 OK" in res.text


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


# ---------------------------------------------------------------------------
# Tests: the final security-header set per layer, on the wire
# ---------------------------------------------------------------------------

# Every header this suite pins. A response carries exactly the expected subset, each at most once.
_SECURITY_HEADERS: Final[frozenset[str]] = frozenset(
    h.lower()
    for h in (
        "Content-Security-Policy",
        "Cross-Origin-Opener-Policy",
        "Cross-Origin-Resource-Policy",
        "Permissions-Policy",
        "Referrer-Policy",
        "Reporting-Endpoints",
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "X-Frame-Options",
    )
)
# Over plain http: no HSTS (the edge sets it; the backend only over https), no Reporting-Endpoints
# (opt-in through CSP_REPORTING_ENABLED, off in the hermetic stack).
_FRONTEND_LAYER: Final[dict[str, str]] = {
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}
_NGINX_LAYER: Final[dict[str, str]] = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'",
}


def _stack_client(port_var: str, default_port: str) -> httpx.Client:
    host = os.getenv("FRONTEND_HOST" if port_var == "FRONTEND_PORT" else "NGINX_HOST", "127.0.0.1")
    base = f"http://{host}:{os.getenv(port_var, default_port)}"
    return httpx.Client(base_url=base, timeout=10.0, trust_env=False)


def _security(res: httpx.Response) -> dict[str, str]:
    """The pinned headers of a response; fails when one is sent more than once."""
    seen: dict[str, list[str]] = {}
    for name, value in res.headers.multi_items():
        if name.lower() in _SECURITY_HEADERS:
            seen.setdefault(name.lower(), []).append(value)
    duplicated = {k: v for k, v in seen.items() if len(v) > 1}
    assert not duplicated, f"sent more than once: {duplicated}"
    return {k: v[0] for k, v in seen.items()}


def _csp_without_nonce(value: str) -> str:
    return re.sub(r"'nonce-[^']+'", "'nonce-X'", value)


_NEXT_CSP: Final[str] = (
    "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; "
    "script-src 'self' 'nonce-X'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
)


@pytest.mark.live
class TestFinalHeaderSet:
    """Page, API, static asset, nginx file and 404: the exact set, nothing twice."""

    def _page_headers(self, path: str) -> dict[str, str]:
        with _stack_client("FRONTEND_PORT", "3000") as client:
            res = client.get(path)
        got = _security(res)
        assert _csp_without_nonce(got.pop("content-security-policy")) == _NEXT_CSP
        return got

    def test_a_page(self) -> None:
        got = self._page_headers("/")
        assert got == {
            **_FRONTEND_LAYER,
            "permissions-policy": "geolocation=(), microphone=(), camera=()",
        }

    def test_a_404_page(self) -> None:
        got = self._page_headers("/no-such-page-4f9a")
        assert got == {
            **_FRONTEND_LAYER,
            "permissions-policy": "geolocation=(), microphone=(), camera=()",
        }

    def test_an_api_response_through_the_frontend(self) -> None:
        with _stack_client("FRONTEND_PORT", "3000") as client:
            res = client.get(f"{_api_base()}/health")
        assert res.status_code == status.HTTP_200_OK
        got = _security(res)
        assert _csp_without_nonce(got.pop("content-security-policy")) == _NEXT_CSP
        # The backend's Permissions-Policy is the one that reaches the browser (one value, once).
        assert got == {
            **_FRONTEND_LAYER,
            "permissions-policy": "camera=(), microphone=(), geolocation=()",
            "x-frame-options": "DENY",
        }

    def _chunk_path(self) -> str:
        with _stack_client("FRONTEND_PORT", "3000") as client:
            html = client.get("/").text
        match = re.search(r'/_next/static/[^"\\]+\.js', html)
        assert match, "the page references no static chunk"
        return match.group(0)

    def test_a_static_asset_through_the_frontend(self) -> None:
        with _stack_client("FRONTEND_PORT", "3000") as client:
            res = client.get(self._chunk_path())
        assert res.status_code == status.HTTP_200_OK
        assert _security(res) == {
            **_FRONTEND_LAYER,
            "permissions-policy": "geolocation=(), microphone=(), camera=()",
        }

    def test_nginx_files_and_404(self) -> None:
        with _stack_client("NGINX_PORT", "8081") as client:
            for path, code in (
                ("/health.txt", status.HTTP_200_OK),
                (self._chunk_path(), status.HTTP_200_OK),
                ("/no-such-file-4f9a", status.HTTP_404_NOT_FOUND),
                ("/healthz", status.HTTP_200_OK),
            ):
                res = client.get(path)
                assert res.status_code == code, path
                assert _security(res) == _NGINX_LAYER, path
