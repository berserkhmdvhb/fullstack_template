# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from your_service_name.backend.main import _parse_origins, create_app


def test_create_app_defaults() -> None:
    app = create_app()
    assert isinstance(app, FastAPI)
    assert app.title == "Backend Title"
    assert app.description == "Minimal FastAPI backend"
    tag_names = {t["name"] for t in (app.openapi_tags or [])}
    assert {"health", "echo"} <= tag_names


def test_lifespan_initializes_echo_service() -> None:
    app = create_app()
    # Use TestClient to enter lifespan; then assert on the running app's state
    with TestClient(app) as client:
        running_app = client.app  # FastAPI at runtime
        # Avoid static typing on TestClient.app (ASGI app) by checking at runtime
        assert isinstance(running_app, FastAPI)
        assert hasattr(running_app.state, "echo_service")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        (" https://a.com , https://b.com ", ["https://a.com", "https://b.com"]),
        ("https://a.com,https://b.com", ["https://a.com", "https://b.com"]),
        ("https://a.com,, https://b.com", ["https://a.com", "https://b.com"]),
    ],
)
def test_parse_origins(raw: str | None, expected: list[str]) -> None:
    assert _parse_origins(raw) == expected


def test_cors_star_removed_when_credentials_true(
    monkeypatch: pytest.MonkeyPatch, api_base: str
) -> None:
    """
    Instead of inspecting app.user_middleware (which confuses mypy),
    we validate CORS behavior via a preflight request:
      - When credentials=true and origins include "*", the effective allow-origin must not be "*"
      - For a specific Origin, the response must echo that origin
    """
    monkeypatch.setenv("CORS_ALLOW_CREDENTIALS", "true")
    monkeypatch.setenv("CORS_ORIGINS", "*, https://a.com")
    app = create_app()

    # Simulate a browser preflight
    with TestClient(app) as client:
        resp = client.options(
            f"{api_base}/health",
            headers={
                "Origin": "https://a.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        # Starlette's CORSMiddleware returns 200 on preflight when configured
        assert resp.status_code in (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT)
        # The middleware should echo the concrete origin, not "*"
        allow_origin = resp.headers.get("access-control-allow-origin", "")
        assert allow_origin == "https://a.com"


def test_api_router_is_included(api_base: str) -> None:
    """
    Rather than introspecting BaseRoute.path (which mypy doesn't like),
    we validate router presence by calling a known health endpoint.
    """
    app = create_app()
    with TestClient(app) as client:
        r = client.get(f"{api_base}/health")
        assert r.status_code == status.HTTP_200_OK


def test_docs_serves_the_swagger_ui() -> None:
    # /docs must actually SERVE the interactive Swagger UI docs as HTML — not 404,
    # not a bare redirect. Assert the resolved 200, the HTML content type, and the
    # framework marker in the body so a misconfigured or disabled docs route fails
    # rather than passing on a redirect.
    #
    # FastAPI serves it from the jsDelivr CDN (bundle + inline init script), so the
    # page is not CSP-clean by itself: the frontend admits that CDN origin and
    # inline script ONLY on /docs via a scoped CSP relaxation (see middleware.ts).
    app = create_app()
    with TestClient(app) as client:
        docs = client.get("/docs")
        assert docs.status_code == status.HTTP_200_OK
        assert docs.headers["content-type"].startswith("text/html")
        assert "swagger-ui" in docs.text.lower()
