# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
The interactive API docs (Swagger UI): the page at ``docs_url`` and its init script beside it.

Replaces FastAPI's built-in page, which inlines a script (see ``api/docs_page.py``). Opt-in through
``Settings.docs_url`` / ``Settings.openapi_url``; both unset means the routes do not exist.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from your_service_name.backend.api import docs_page


def build_docs_router(*, title: str, docs_url: str, openapi_url: str) -> APIRouter:
    """Routes for ``docs_url`` (HTML) and ``<docs_url>/init.js``; not in the OpenAPI document."""
    init_url = f"{docs_url.rstrip('/')}/init.js"
    page = HTMLResponse(docs_page.render_html(title=f"{title} - Swagger UI", init_url=init_url))
    router = APIRouter(include_in_schema=False)

    @router.get(docs_url)
    async def swagger_ui() -> Response:
        return page

    @router.get(init_url)
    async def swagger_ui_init() -> Response:
        return Response(docs_page.render_init_js(openapi_url), media_type="text/javascript")

    return router
