# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The Swagger UI page: no inline script, pinned assets with SRI, off when docs are off."""

from __future__ import annotations

import re
from http import HTTPStatus

import httpx
import pytest

from your_service_name.backend.api import docs_page
from your_service_name.backend.main import create_app
from your_service_name.backend.settings import Settings

SCRIPT_OR_LINK = re.compile(r"<(?:script|link)\b[^>]*>", re.IGNORECASE)


async def _get(path: str, **settings: object) -> httpx.Response:
    app = create_app(Settings(ENV="dev", **settings))  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path)


@pytest.mark.asyncio
async def test_the_page_has_no_inline_script() -> None:
    res = await _get("/docs")
    assert res.status_code == HTTPStatus.OK
    assert "text/html" in res.headers["content-type"]
    bodies = re.findall(r"<script\b[^>]*>(.*?)</script>", res.text, re.DOTALL | re.IGNORECASE)
    assert all(not body.strip() for body in bodies), bodies
    assert not re.search(r"\son\w+\s*=", res.text), "an inline event handler"
    assert "fastapi.tiangolo.com" not in res.text
    assert 'href="/favicon.ico"' in res.text


@pytest.mark.asyncio
async def test_every_external_script_and_stylesheet_is_pinned_with_sri() -> None:
    res = await _get("/docs")
    tags = [t for t in SCRIPT_OR_LINK.findall(res.text) if "cdn.jsdelivr.net" in t]
    assert len(tags) == len(("stylesheet", "bundle"))
    for tag in tags:
        assert re.search(r'integrity="sha384-[A-Za-z0-9+/]{64}"', tag), tag
        assert 'crossorigin="anonymous"' in tag, tag
        assert f"swagger-ui-dist@{docs_page.SWAGGER_UI_VERSION}/" in tag, tag


def test_the_version_is_an_exact_release() -> None:
    assert re.fullmatch(r"5\.\d+\.\d+", docs_page.SWAGGER_UI_VERSION)
    assert docs_page.SWAGGER_UI_VERSION in docs_page.SWAGGER_UI_CSS_URL
    assert docs_page.SWAGGER_UI_VERSION in docs_page.SWAGGER_UI_JS_URL


@pytest.mark.asyncio
async def test_the_init_script_points_at_the_openapi_document() -> None:
    res = await _get("/docs/init.js")
    assert res.status_code == HTTPStatus.OK
    assert res.headers["content-type"].startswith("text/javascript")
    assert 'url: "/openapi.json"' in res.text
    assert 'dom_id: "#swagger-ui"' in res.text
    page = await _get("/docs")
    assert 'src="/docs/init.js"' in page.text


@pytest.mark.asyncio
async def test_a_custom_docs_path_moves_both_routes() -> None:
    page = await _get("/api-docs", API_DOCS_URL="/api-docs")
    assert page.status_code == HTTPStatus.OK
    assert 'src="/api-docs/init.js"' in page.text
    assert (await _get("/api-docs/init.js", API_DOCS_URL="/api-docs")).status_code == HTTPStatus.OK
    assert (await _get("/docs", API_DOCS_URL="/api-docs")).status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/docs/init.js", "/docs/oauth2-redirect"])
async def test_the_routes_are_absent_when_docs_are_off(path: str) -> None:
    assert (await _get(path, API_DOCS_URL="")).status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_the_pages_are_not_in_the_openapi_document() -> None:
    paths = (await _get("/openapi.json")).json()["paths"]
    assert not [p for p in paths if p.startswith("/docs")]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/docs/init.js"])
async def test_the_docs_send_their_own_strict_csp_once(path: str) -> None:
    res = await _get(path)
    assert res.headers.get_list("content-security-policy") == [docs_page.CSP]
    directives = dict(d.split(" ", 1) for d in docs_page.CSP.split("; "))
    assert "unsafe-" not in docs_page.CSP
    assert directives["script-src"] == "'self' https://cdn.jsdelivr.net"
    assert directives["style-src"] == "'self' https://cdn.jsdelivr.net"
    assert directives["frame-ancestors"] == "'none'"
    assert directives["base-uri"] == "'self'"
    assert directives["form-action"] == "'self'"  # no default-src fallback (ZAP rule 10055)
    assert directives["connect-src"] == "'self'"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/docs/init.js"])
async def test_the_docs_csp_reports_violations_when_reporting_is_on(path: str) -> None:
    res = await _get(path, CSP_REPORT_ENABLED=True)
    assert res.headers.get_list("content-security-policy") == [
        f"{docs_page.CSP}; report-uri /api/v1/csp-report; report-to csp-endpoint"
    ]
    assert res.headers.get_list("reporting-endpoints") == ['csp-endpoint="/api/v1/csp-report"']


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/docs", "/docs/init.js"])
async def test_the_docs_csp_has_no_reporting_when_it_is_off(path: str) -> None:
    res = await _get(path)
    assert "report-" not in res.headers["content-security-policy"]
    assert "reporting-endpoints" not in res.headers
