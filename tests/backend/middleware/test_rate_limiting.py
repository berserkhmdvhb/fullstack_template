# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Unit tests for the rate limiting middleware.

Covers:
- ``_parse_limit``: parses ``limits`` specs; rejects invalid ones loudly.
- ``exempt_paths``: the exact exempt paths.
- ``_client_ip``: key extraction with/without a client address.
- ``RateLimitMiddleware`` on a minimal app: the (limit+1)-th request is 429 with a
  ``Retry-After`` header, and exempt paths are never throttled.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request, status
from fastapi.testclient import TestClient
from starlette.datastructures import Address

from your_service_name.backend import paths
from your_service_name.backend.middleware.rate_limiting import (
    RateLimitMiddleware,
    _client_ip,
    _parse_limit,
    register_rate_limiting,
)
from your_service_name.backend.settings import Settings

LIMIT_SPEC: str = "100/minute"
LIMIT_AMOUNT: int = 100


def _make_app(limit: str = "3/minute") -> FastAPI:
    """Minimal app with the rate-limit middleware mounted."""
    app = FastAPI()

    @app.get("/thing")
    def thing() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(RateLimitMiddleware, limit=limit, exempt_paths=frozenset({"/health"}))
    return app


class TestParseLimit:
    """``_parse_limit`` validates the configured limit."""

    def test_parses_valid_limit(self) -> None:
        """A valid ``limits`` spec parses to a rate-limit item."""
        item = _parse_limit(LIMIT_SPEC)
        assert item.amount == LIMIT_AMOUNT
        assert item.GRANULARITY.name == "minute"

    def test_rejects_invalid_limit(self) -> None:
        """A garbage spec raises ValueError (fail loud at startup)."""
        with pytest.raises(ValueError, match="Invalid RATE_LIMIT_DEFAULT"):
            _parse_limit("not-a-limit")


class TestExemptPaths:
    """``exempt_paths`` lists exact paths: the versioned probes and the metrics endpoint."""

    def test_lists_the_probes_and_metrics_for_the_version(self) -> None:
        """The probe paths follow the API version; metrics is unversioned."""
        assert paths.exempt_paths("v7") == {"/api/v7/health", "/api/v7/healthz", "/metrics"}

    def test_a_path_ending_like_a_probe_is_not_exempt(self) -> None:
        """No suffix matching: a route that happens to end in /health is limited."""
        assert "/api/v1/items/metrics" not in paths.exempt_paths("v1")
        assert "/x/health" not in paths.exempt_paths("v1")


class TestClientIp:
    """``_client_ip`` extracts the rate-limit key."""

    def test_uses_client_host(self) -> None:
        """The client host is used when present."""
        request = Request({"type": "http", "client": Address("203.0.113.7", 5000)})
        assert _client_ip(request) == "203.0.113.7"

    def test_falls_back_to_loopback(self) -> None:
        """Loopback is used when no client address is present."""
        request = Request({"type": "http", "client": None})
        assert _client_ip(request) == "127.0.0.1"


class TestRateLimitEnforcement:
    """The middleware throttles by client IP and exempts configured paths."""

    def test_burst_beyond_limit_is_429(self) -> None:
        """The (limit+1)-th request in the window is rejected with 429."""
        with TestClient(_make_app("3/minute")) as client:
            for _ in range(3):
                assert client.get("/thing").status_code == status.HTTP_200_OK
            res = client.get("/thing")
            assert res.status_code == status.HTTP_429_TOO_MANY_REQUESTS
            assert res.json()["detail"]
            assert res.headers["Retry-After"]

    def test_exempt_path_not_throttled(self) -> None:
        """Exempt paths stay reachable after the limit is exhausted."""
        with TestClient(_make_app("3/minute")) as client:
            for _ in range(3):
                assert client.get("/thing").status_code == status.HTTP_200_OK
            # Normal path is now limited...
            assert client.get("/thing").status_code == status.HTTP_429_TOO_MANY_REQUESTS
            # ...but the health path is exempt.
            assert client.get("/health").status_code == status.HTTP_200_OK

    def test_register_enforces_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``register_rate_limiting`` mounts a middleware that actually throttles."""
        monkeypatch.setenv("RATE_LIMIT_DEFAULT", "2/minute")
        settings = Settings()
        app = FastAPI()

        @app.get("/thing")
        def thing() -> dict[str, str]:
            return {"ok": "true"}

        register_rate_limiting(app, settings)

        with TestClient(app) as client:
            assert client.get("/thing").status_code == status.HTTP_200_OK
            assert client.get("/thing").status_code == status.HTTP_200_OK
            # The third request in the window is rejected.
            assert client.get("/thing").status_code == status.HTTP_429_TOO_MANY_REQUESTS
