# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Unit tests of the live header-set comparison helper, on canned responses (no stack needed).

The helper lives in ``tests/live/test_header_set.py``, which is live-marked and runs only in CI's
live job; these tests run in ``make test``. The comparison is strict both ways.
"""

from __future__ import annotations

import httpx
import pytest

from tests.live.test_header_set import _NGINX_LAYER, _assert_final_set

_NGINX_WIRE = [
    ("Server", "nginx"),
    ("Date", "x"),
    ("Content-Type", "text/plain"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'none'"),
]


def _canned(extra: list[tuple[str, str]] | None = None, drop: str | None = None) -> httpx.Response:
    headers = [(k, v) for k, v in _NGINX_WIRE if k != drop] + (extra or [])
    return httpx.Response(200, headers=headers)


def test_the_helper_accepts_nginx_with_its_versionless_server_header() -> None:
    _assert_final_set(_canned(), _NGINX_LAYER)


@pytest.mark.parametrize(
    "extra",
    [
        [("Server", "nginx/1.27.0")],  # a version; replaces the plain header
        [("Cross-Origin-Embedder-Policy", "require-corp")],
        [("X-Powered-By", "Next.js")],
        [("X-Middleware-Rewrite", "http://backend:8000/")],
    ],
)
def test_the_helper_still_fails_on_any_other_header_or_a_versioned_server(
    extra: list[tuple[str, str]],
) -> None:
    drop = "Server" if extra[0][0] == "Server" else None
    with pytest.raises(AssertionError):
        _assert_final_set(_canned(extra, drop=drop), _NGINX_LAYER)


def test_the_helper_fails_when_nginx_sends_no_server_header() -> None:
    with pytest.raises(AssertionError):
        _assert_final_set(_canned(drop="Server"), _NGINX_LAYER)


def test_the_helper_fails_on_a_repeated_header() -> None:
    with pytest.raises(AssertionError, match="more than once"):
        _assert_final_set(_canned([("Cache-Control", "a"), ("Cache-Control", "b")]), _NGINX_LAYER)
