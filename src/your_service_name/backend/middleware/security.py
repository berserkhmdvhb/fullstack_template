# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Security headers middleware for the FastAPI backend.

Responsibilities:
- Inject safe, API-friendly HTTP response security headers.
- Provide a single registration function for easy integration.

Public API:
- `register_security_middleware`: Attach the security middleware to a FastAPI app.

Operational:
- Concurrency: Implemented as an async dispatch callable via Starlette middleware.
- Logging: No logging by default (headers are set silently per request).

Usage
-----
    from fastapi import FastAPI
    from your_service_name.backend.middleware.security import register_security_middleware

    app = FastAPI()
    register_security_middleware(app)

Notes
-----
- Headers set by default:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: no-referrer`
  - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
  - `Cross-Origin-Resource-Policy: same-origin` (blocks other origins reading the
    JSON body via e.g. `<script>`/`<img>` — defense-in-depth beyond CORS)
- HSTS (`Strict-Transport-Security`) is sent over https only, and only while
  the `HSTS_ENABLED` setting is on: turn it off where the edge sets HSTS, so a
  deployment has one source of it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

if TYPE_CHECKING:
    from fastapi import FastAPI

# Type alias for the ASGI dispatch callable that returns a Response.
Dispatch = Callable[[Request], Awaitable[Response]]


async def _security_headers_dispatch(
    request: Request, call_next: Dispatch, *, hsts: bool = True
) -> Response:
    """
    Apply a minimal set of HTTP security headers to responses.

    Parameters
    ----------
    request : starlette.requests.Request
        Incoming HTTP request.
    call_next : Callable[[Request], Awaitable[Response]]
        The next ASGI handler that must be awaited to obtain a response.
    hsts : bool
        Send HSTS over https (the `HSTS_ENABLED` setting); off where the edge sets it.

    Returns
    -------
    starlette.responses.Response
        The outgoing response with security headers applied.

    Notes
    -----
    - The following headers are added if not already present:
      * `X-Content-Type-Options: nosniff`
      * `X-Frame-Options: DENY`
      * `Referrer-Policy: no-referrer`
      * `Permissions-Policy: camera=(), microphone=(), geolocation=()`
      * `Cross-Origin-Resource-Policy: same-origin`
    - HSTS is added over https only, and only when ``hsts`` is on.

    Examples
    --------
    >>> # This function is used indirectly via BaseHTTPMiddleware.
    >>> # You normally don't call it yourself; use `register_security_middleware`.
    """
    response = await call_next(request)

    # Minimal, API-friendly defaults
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")

    # Defense-in-depth: harden the response even when the service is served
    # without an edge in front. `Permissions-Policy` disables powerful browser
    # features; `Cross-Origin-Resource-Policy: same-origin` stops other origins
    # from embedding/reading the JSON body (e.g. via <img>/<script>), complementing
    # CORS. setdefault() so an explicit upstream header is never clobbered.
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")

    # HSTS: only set when the connection is HTTPS (TLS terminated upstream), and only when the
    # edge does not set it (one source per deployment). Over plain HTTP, HSTS would be incorrect.
    if hsts and request.scope.get("scheme") == "https":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains; preload",
        )
    return response


def register_security_middleware(app: FastAPI, *, hsts: bool = True) -> None:
    """
    Register the security headers middleware on the given FastAPI application.

    Parameters
    ----------
    app : fastapi.FastAPI
        The FastAPI application instance to modify.
    hsts : bool
        Send HSTS over https (default on); pass ``settings.HSTS_ENABLED``.

    Examples
    --------
    >>> from fastapi import FastAPI
    >>> app = FastAPI()
    >>> register_security_middleware(app)
    """
    app.add_middleware(BaseHTTPMiddleware, dispatch=partial(_security_headers_dispatch, hsts=hsts))
