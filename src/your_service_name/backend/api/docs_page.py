# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
The interactive API docs page: Swagger UI without an inline script.

FastAPI's built-in page inlines its init script, which a strict ``script-src`` (nonce, no
``'unsafe-inline'``) blocks. This page loads Swagger UI's CSS and bundle from jsDelivr at one exact
version, each pinned by a Subresource Integrity hash, and its init script from the backend itself.

Updating Swagger UI: run ``scripts/swagger-ui-sri.sh <version>`` and paste the version and the two
hashes below (``wiki/backend-architecture.md``). A version bump without new hashes makes the browser
refuse the files, and the ``/docs`` Playwright test fails.
"""

from __future__ import annotations

import html
import json
from typing import Final

SWAGGER_UI_VERSION: Final[str] = "5.35.1"
SWAGGER_UI_CSS_SRI: Final[str] = (
    "sha384-WlYgdmXYOOBWL5apwjDEZwN+U3HmXSSu5R6V5aGxEcf7z+KuRNwBhoxKVN1i3D5I"
)
SWAGGER_UI_JS_SRI: Final[str] = (
    "sha384-J5eKRXKO1KzUTPkO0GRmGH2WEYUg6SSZlufilxvTwI6nmhLumjNWFMby3QQnyobZ"
)

_CDN: Final[str] = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"
SWAGGER_UI_CSS_URL: Final[str] = f"{_CDN}/swagger-ui.css"
SWAGGER_UI_JS_URL: Final[str] = f"{_CDN}/swagger-ui-bundle.js"

_PAGE: Final[str] = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<link rel="stylesheet" href="{css_url}" integrity="{css_sri}" crossorigin="anonymous">
<link rel="icon" href="/favicon.ico">
<title>{title}</title>
</head>
<body>
<div id="swagger-ui"></div>
<script src="{js_url}" integrity="{js_sri}" crossorigin="anonymous"></script>
<script src="{init_url}"></script>
</body>
</html>
"""

# The options FastAPI's built-in page passes (swagger_ui_default_parameters), plus the presets.
_INIT_JS: Final[str] = """SwaggerUIBundle({{
  url: {openapi_url},
  dom_id: "#swagger-ui",
  layout: "BaseLayout",
  deepLinking: true,
  showExtensions: true,
  showCommonExtensions: true,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset],
}});
"""


def render_html(*, title: str, init_url: str) -> str:
    """The docs page; ``init_url`` is where the backend serves :func:`render_init_js`."""
    return _PAGE.format(
        css_url=SWAGGER_UI_CSS_URL,
        css_sri=SWAGGER_UI_CSS_SRI,
        js_url=SWAGGER_UI_JS_URL,
        js_sri=SWAGGER_UI_JS_SRI,
        init_url=html.escape(init_url, quote=True),
        title=html.escape(title),
    )


def render_init_js(openapi_url: str) -> str:
    """The init script: Swagger UI reads the OpenAPI document at ``openapi_url``."""
    return _INIT_JS.format(openapi_url=json.dumps(openapi_url))
