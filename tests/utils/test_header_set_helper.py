# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Unit tests of the live header-set comparison helper, on canned responses (no stack needed).

The helper lives in ``tests/live/test_header_set.py``, which is live-marked and runs only in CI's
live job; these tests run in ``make test``. The comparison is strict both ways.
"""

from __future__ import annotations

import httpx
import pytest

from tests.live.test_header_set import _FRONTEND_LAYER, _assert_final_set

_WIRE = [
    ("Date", "x"),
    ("Content-Type", "text/plain"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
]


def _canned(extra: list[tuple[str, str]] | None = None, drop: str | None = None) -> httpx.Response:
    headers = [(k, v) for k, v in _WIRE if k != drop] + (extra or [])
    return httpx.Response(200, headers=headers)


def test_the_helper_accepts_the_frontend_set_plus_transport_headers() -> None:
    _assert_final_set(
        _canned([("Cache-Control", "no-cache")]),
        _FRONTEND_LAYER,
        exact_transport={"cache-control": "no-cache"},
    )


@pytest.mark.parametrize(
    "extra",
    [
        [("Server", "nginx")],
        [("Cross-Origin-Embedder-Policy", "require-corp")],
        [("X-Powered-By", "Next.js")],
        [("X-Middleware-Rewrite", "http://backend:8000/")],
    ],
)
def test_the_helper_fails_on_any_other_header(extra: list[tuple[str, str]]) -> None:
    with pytest.raises(AssertionError):
        _assert_final_set(_canned(extra), _FRONTEND_LAYER)


def test_the_helper_fails_when_a_security_header_is_missing() -> None:
    with pytest.raises(AssertionError):
        _assert_final_set(_canned(drop="X-Content-Type-Options"), _FRONTEND_LAYER)


def test_the_helper_fails_when_an_exact_transport_value_differs() -> None:
    with pytest.raises(AssertionError):
        _assert_final_set(
            _canned([("Cache-Control", "no-store")]),
            _FRONTEND_LAYER,
            exact_transport={"cache-control": "no-cache"},
        )


def test_the_helper_fails_on_a_repeated_header() -> None:
    with pytest.raises(AssertionError, match="more than once"):
        _assert_final_set(
            _canned([("Cache-Control", "a"), ("Cache-Control", "b")]), _FRONTEND_LAYER
        )
