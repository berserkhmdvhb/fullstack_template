# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

from fastapi import FastAPI, Response, status
from fastapi.testclient import TestClient

from your_service_name.backend.middleware.security import register_security_middleware


def _make_app() -> FastAPI:
    app = FastAPI()
    register_security_middleware(app)

    @app.get("/plain")
    def plain() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/custom")
    def custom() -> Response:
        # Simulate a route that sets its own header values; middleware should not override them
        resp = Response(content="custom")
        resp.headers["X-Content-Type-Options"] = "custom-nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "strict-origin"
        resp.headers["Strict-Transport-Security"] = "max-age=123"
        resp.headers["Permissions-Policy"] = "camera=(self)"
        resp.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        return resp

    return app


def test_security_headers_default_present() -> None:
    app = _make_app()
    with TestClient(app) as client:
        r = client.get("/plain")
        assert r.status_code == status.HTTP_200_OK
        # Defaults injected by middleware (via setdefault)
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        # Defense-in-depth defaults (set via setdefault, so API-friendly even
        # without an NGINX edge): disable powerful features; block cross-origin
        # embedding/reading of the JSON body.
        assert r.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
        assert r.headers["Cross-Origin-Resource-Policy"] == "same-origin"
        # HSTS is only set for HTTPS connections; plain HTTP test client
        # requests should NOT have HSTS (avoids breaking HTTP-only setups).
        assert "Strict-Transport-Security" not in r.headers


def test_security_headers_not_overridden_when_present() -> None:
    app = _make_app()
    with TestClient(app) as client:
        r = client.get("/custom")
        assert r.status_code == status.HTTP_200_OK
        # Route-set headers remain, because middleware uses setdefault(...)
        assert r.headers["X-Content-Type-Options"] == "custom-nosniff"
        assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
        assert r.headers["Referrer-Policy"] == "strict-origin"
        assert r.headers["Strict-Transport-Security"] == "max-age=123"
        # Route-set values for the setdefault() headers are likewise preserved.
        assert r.headers["Permissions-Policy"] == "camera=(self)"
        assert r.headers["Cross-Origin-Resource-Policy"] == "cross-origin"
