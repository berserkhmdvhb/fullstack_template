# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Container HEALTHCHECK probe: ``python -m your_service_name.backend.healthcheck``.

Requests ``${HEALTHCHECK_SCHEME:-http}://localhost:${BACKEND_PORT:-8000}/api/${API_VERSION:-v1}/healthz``
and exits 0 on a 2xx, 1 on anything else (non-2xx, a redirect, connection refused, a timeout, a
TLS failure). The image has no curl and no shell, so the probe is Python.

Import-light on purpose: standard library only. Importing it must not load FastAPI, SQLAlchemy,
pydantic or the app, because it runs every few seconds in the container. For the same reason it
reads the environment itself instead of going through ``Settings``.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

# Below the 5 s the Dockerfile HEALTHCHECK allows, so the probe answers before Docker gives up.
TIMEOUT_SECONDS: float = 4
_OK_LOW: Final = 200
_OK_HIGH: Final = 300


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx is an unhealthy answer, not a hop to follow."""

    def redirect_request(self, *_: object, **__: object) -> None:
        return None


def url(environ: Mapping[str, str]) -> str:
    """The liveness URL, from the same variables the launcher and the image use."""
    scheme = environ.get("HEALTHCHECK_SCHEME") or "http"
    port = environ.get("BACKEND_PORT") or "8000"
    version = environ.get("API_VERSION") or "v1"
    return f"{scheme}://localhost:{port}/api/{version}/healthz"


def main() -> int:
    """0 when the backend answers 2xx, else 1."""
    target = url(os.environ)
    if not target.startswith(("http://", "https://")):
        return 1
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(target, timeout=TIMEOUT_SECONDS) as response:
            return 0 if _OK_LOW <= response.status < _OK_HIGH else 1
    except Exception:  # noqa: BLE001 - any failure at all means unhealthy
        return 1


if __name__ == "__main__":
    sys.exit(main())
