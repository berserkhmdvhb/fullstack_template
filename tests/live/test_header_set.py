# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Live tests: the final response headers per layer, on the wire.

Targets the frontend port (FRONTEND_PORT, the app and the proxied API) and the nginx port
(NGINX_PORT, static files) of the hermetic stack that ``entrypoint.sh --mode all`` starts.

Every header of a response must be an expected security header (exact value, exactly once) or a
non-security transport header from the allowlist below. A new header from any layer therefore
fails here until it is named on purpose.
"""

from __future__ import annotations

import os
import re
from typing import Final

import httpx
import pytest
from fastapi import status

pytestmark = pytest.mark.live

# Non-security headers real responses carry: HTTP transport, caching, nginx/uvicorn identity, the
# request id, Next.js's own routing headers (x-middleware-rewrite on proxied paths, x-nextjs-*).
_TRANSPORT: Final[frozenset[str]] = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expires",
        "keep-alive",
        "last-modified",
        "link",
        "location",
        "server",
        "transfer-encoding",
        "vary",
        "x-middleware-rewrite",
        "x-request-id",
    }
)
_TRANSPORT_PREFIXES: Final[tuple[str, ...]] = ("x-nextjs-",)

_NEXT_CSP: Final[str] = (
    "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; "
    "script-src 'self' 'nonce-X'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
)
_FRONTEND_LAYER: Final[dict[str, str]] = {
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "permissions-policy": "geolocation=(), microphone=(), camera=()",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
}
_NGINX_LAYER: Final[dict[str, str]] = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'",
}


def _client(port_var: str, default_port: str, host_var: str) -> httpx.Client:
    host = os.getenv(host_var, "127.0.0.1")
    base = f"http://{host}:{os.getenv(port_var, default_port)}"
    return httpx.Client(base_url=base, timeout=10.0, trust_env=False)


def _frontend() -> httpx.Client:
    return _client("FRONTEND_PORT", "3000", "FRONTEND_HOST")


def _nginx() -> httpx.Client:
    return _client("NGINX_PORT", "8081", "NGINX_HOST")


def _final_headers(res: httpx.Response) -> dict[str, str]:
    """All headers by lower-case name, nonces normalised; fails on a repeated header."""
    names = [name.lower() for name, _ in res.headers.multi_items()]
    repeated = sorted({n for n in names if names.count(n) > 1})
    assert not repeated, f"sent more than once: {repeated}"
    return {
        name.lower(): re.sub(r"'nonce-[^']+'", "'nonce-X'", value)
        for name, value in res.headers.multi_items()
    }


def _assert_final_set(res: httpx.Response, expected: dict[str, str]) -> None:
    got = _final_headers(res)
    security = {
        k: v
        for k, v in got.items()
        if k not in _TRANSPORT and not k.startswith(_TRANSPORT_PREFIXES)
    }
    assert security == expected


def _chunk_path() -> str:
    with _frontend() as client:
        html = client.get("/").text
    match = re.search(r'/_next/static/[^"\\]+\.js', html)
    assert match, "the page references no static chunk"
    return match.group(0)


def test_a_page() -> None:
    with _frontend() as client:
        res = client.get("/")
    assert res.status_code == status.HTTP_200_OK
    _assert_final_set(res, {**_FRONTEND_LAYER, "content-security-policy": _NEXT_CSP})


def test_a_404_page() -> None:
    with _frontend() as client:
        res = client.get("/no-such-page-4f9a")
    assert res.status_code == status.HTTP_404_NOT_FOUND
    _assert_final_set(res, {**_FRONTEND_LAYER, "content-security-policy": _NEXT_CSP})


def test_an_api_response_through_the_frontend() -> None:
    with _frontend() as client:
        res = client.get(f"/api/{os.getenv('API_VERSION', 'v1')}/health")
    assert res.status_code == status.HTTP_200_OK
    # The backend's Permissions-Policy is the one that reaches the browser (one value, once).
    _assert_final_set(
        res,
        {
            **_FRONTEND_LAYER,
            "permissions-policy": "camera=(), microphone=(), geolocation=()",
            "content-security-policy": _NEXT_CSP,
            "x-frame-options": "DENY",
        },
    )


def test_a_static_asset_through_the_frontend() -> None:
    with _frontend() as client:
        res = client.get(_chunk_path())
    assert res.status_code == status.HTTP_200_OK
    _assert_final_set(res, _FRONTEND_LAYER)


@pytest.mark.parametrize("path", ["/health.txt", "/healthz", "/no-such-file-4f9a", "chunk"])
def test_nginx_files_and_404(path: str) -> None:
    expected = status.HTTP_404_NOT_FOUND if "no-such" in path else status.HTTP_200_OK
    with _nginx() as client:
        res = client.get(_chunk_path() if path == "chunk" else path)
    assert res.status_code == expected
    _assert_final_set(res, _NGINX_LAYER)
