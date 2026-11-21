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

from your_service_name.backend.api import docs_page

pytestmark = pytest.mark.live

# Non-security headers real responses carry: HTTP transport, caching, the request id, Next.js's own
# routing headers (x-nextjs-*). `server` is not here: it is pinned per layer (nginx: exactly
# "nginx", no version; the backend and the frontend: absent).
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
        "keep-alive",
        "last-modified",
        "link",
        "location",
        "transfer-encoding",
        "vary",
        "x-request-id",
    }
)
# Headers Next.js itself adds to the responses it proxies to the backend, so they appear only on the
# frontend-direct port (dev, `next start`), only on proxied paths, and never through the gateway
# (nginx hides them). Next 16.4.6 always sets x-middleware-rewrite to the rewrite destination, the
# internal backend URL: node_modules/next/dist/server/lib/router-utils/resolve-routes.js:468-471 and
# router-server.js:448-450. Deleting it in proxy.ts cancels the rewrite: not removable there.
KNOWN_NEXT_EXPOSURES: Final[frozenset[str]] = frozenset({"x-middleware-rewrite"})
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
    # `server_tokens off`: nginx still names itself, with no version.
    "server": "nginx",
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


def _assert_final_set(
    res: httpx.Response,
    expected: dict[str, str],
    *,
    proxied_by_next: bool = False,
    exact_transport: dict[str, str] | None = None,
) -> None:
    """The response carries ``expected`` exactly, plus transport headers and nothing else.

    ``proxied_by_next``: the path is proxied by the frontend, so a known Next exposure may appear.
    ``exact_transport``: transport headers whose value is part of the contract.
    """
    got = _final_headers(res)
    exposed = KNOWN_NEXT_EXPOSURES & got.keys()
    assert proxied_by_next or not exposed, (
        f"a Next-internal header on a non-proxied path: {exposed}"
    )
    security = {
        k: v
        for k, v in got.items()
        if k not in _TRANSPORT
        and k not in KNOWN_NEXT_EXPOSURES
        and not k.startswith(_TRANSPORT_PREFIXES)
    }
    assert security == expected
    for name, value in (exact_transport or {}).items():
        assert got.get(name) == value, name


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
        proxied_by_next=True,
    )


@pytest.mark.parametrize("path", ["/docs", "/docs/init.js"])
def test_the_docs_through_the_frontend_carry_the_backends_one_csp(path: str) -> None:
    with _frontend() as client:
        res = client.get(path)
    assert res.status_code == status.HTTP_200_OK
    _assert_final_set(
        res,
        {
            **_FRONTEND_LAYER,
            "permissions-policy": "camera=(), microphone=(), geolocation=()",
            "content-security-policy": docs_page.CSP,
            "x-frame-options": "DENY",
        },
        proxied_by_next=True,
    )
    assert "server" not in res.headers


def test_the_frontend_pages_expose_no_server_or_next_internals() -> None:
    with _frontend() as client:
        for path in ("/", "/no-such-page-4f9a", "/api/healthz"):
            res = client.get(path)
            assert "server" not in res.headers, path
            assert not KNOWN_NEXT_EXPOSURES & set(_final_headers(res)), path


def test_a_backend_response_has_no_server_header() -> None:
    host = os.getenv("BACKEND_HOST", "127.0.0.1")
    port = os.getenv("BACKEND_PORT", "8000")
    with httpx.Client(base_url=f"http://{host}:{port}", timeout=10.0, trust_env=False) as client:
        res = client.get(f"/api/{os.getenv('API_VERSION', 'v1')}/health")
    assert res.status_code == status.HTTP_200_OK
    assert "server" not in res.headers


def test_a_static_asset_through_the_frontend() -> None:
    with _frontend() as client:
        res = client.get(_chunk_path())
    assert res.status_code == status.HTTP_200_OK
    _assert_final_set(res, _FRONTEND_LAYER)


_IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.mark.parametrize(
    ("path", "cache_control"),
    [
        ("/health.txt", "no-cache"),
        ("/healthz", None),
        ("/no-such-file-4f9a", None),
        ("chunk", _IMMUTABLE),
    ],
)
def test_nginx_files_and_404(path: str, cache_control: str | None) -> None:
    expected = status.HTTP_404_NOT_FOUND if "no-such" in path else status.HTTP_200_OK
    with _nginx() as client:
        res = client.get(_chunk_path() if path == "chunk" else path)
    assert res.status_code == expected
    # One Cache-Control (a repeat fails in _final_headers), the intended value, a bare nginx.
    pinned = {"cache-control": cache_control} if cache_control else {}
    _assert_final_set(res, _NGINX_LAYER, exact_transport=pinned)
    assert ("cache-control" in res.headers) == (cache_control is not None)
