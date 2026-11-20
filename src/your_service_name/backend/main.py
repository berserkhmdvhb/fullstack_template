# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Minimal FastAPI application wiring routers, middleware, and lifecycle hooks.

Responsibilities
----------------
- Initialize the FastAPI application (factory pattern).
- Configure logging, CORS, and security headers middleware.
- Register feature routers via a central API assembly.
- Initialize dependency injection (DI) inside the lifespan block to ensure services are ready.

Public API
----------
- `create_app`: Construct and configure the FastAPI application.
- `app`: ASGI application instance for Uvicorn/Gunicorn.

Operational
-----------
- Concurrency: Async endpoints; lifespan uses `asynccontextmanager` for startup/shutdown.
- Logging: Configured from `LOG_LEVEL` (environment); logs startup/shutdown events.

Usage
-----
from your_service_name.backend.main import create_app
app = create_app()  # use in tests or custom ASGI servers

Notes
-----
- CORS origins are parsed from the `CORS_ORIGINS` environment variable (comma-separated).
- Security headers are injected via `register_security_middleware`.
- Rate limiting is configured via `register_rate_limiting` (per-IP, `limits`).
- Request logging is opt-in via `LOG_REQUESTS` env var.
- DI initialization occurs inside `lifespan` to guarantee availability for request dependencies.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from your_service_name.backend.api import build_api_router
from your_service_name.backend.db import close_db
from your_service_name.backend.logging.correlation import RequestIdFilter
from your_service_name.backend.logging.json_formatter import JsonLogFormatter
from your_service_name.backend.middleware.correlation import CorrelationIdMiddleware
from your_service_name.backend.middleware.logging import RequestLoggingMiddleware
from your_service_name.backend.middleware.rate_limiting import register_rate_limiting
from your_service_name.backend.middleware.security import register_security_middleware
from your_service_name.backend.repositories.in_memory_echo import InMemoryEchoRepository
from your_service_name.backend.services.echo_service import EchoService
from your_service_name.utils.validators import LogLevel
from your_service_name.utils.versioning_api import API_CONTRACT_VERSION, get_api_version

# --- Bootstrap (runs once, at import time = the process entrypoint) ----------


def _use_json_logs() -> bool:
    """Emit structured JSON logs when explicitly requested (``LOG_FORMAT=json``) or in
    production, where logs are shipped to a central system and must be machine-parseable."""
    return os.getenv("LOG_FORMAT", "").lower() == "json" or os.getenv("ENV", "").lower() in {
        "production",
        "prod",
    }


def _configure_logging() -> None:
    """Configure the root logger exactly once per process.

    ``logging.basicConfig`` is idempotent — it only installs a handler when the root
    logger has none — so this guard makes the intent explicit and safe under repeated
    import / re-entrant calls without a mutable module flag.

    Logs are structured JSON when :func:`_use_json_logs` (production, or ``LOG_FORMAT=json``);
    otherwise a readable single-line text format. Either way a :class:`RequestIdFilter` injects
    the active correlation id (``X-Request-Id``) onto every record.
    """
    if logging.getLogger().handlers:
        return
    log_level = LogLevel.resolve(os.getenv("LOG_LEVEL"))
    root = logging.getLogger()
    handler = logging.StreamHandler()
    if _use_json_logs():
        handler.setFormatter(JsonLogFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s [request_id=%(request_id)s]: %(message)s",
            )
        )
    # On the HANDLER (not the logger): a logger-level filter only runs for records that logger
    # emits itself, so application loggers (children of root) would skip it and the text format's
    # ``%(request_id)s`` would raise KeyError. A handler-level filter runs for every record that
    # passes through — including child loggers propagating up — so all logs carry the id.
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)
    root.setLevel(log_level)


# ``load_dotenv`` runs here — not in create_app() — so the .env is read once per process
# rather than re-loaded (and re-shadowing monkeypatched test env) on every create_app()
# call. It must precede the LOG_LEVEL / API_VERSION reads below.
load_dotenv()
_configure_logging()
_API_VERSION: Final[str] = get_api_version()
logger = logging.getLogger(__name__)


def _parse_csv(raw: str | None, default: list[str]) -> list[str]:
    """
    Parse a comma-separated list into a trimmed list of strings.

    Parameters
    ----------
    raw : str or None
        Raw comma-separated value (e.g. "a, b, c"). Empty/None → ``default``.
    default : list[str]
        Value to use when ``raw`` is empty or None.

    Returns
    -------
    list[str]
        Trimmed, non-empty entries.
    """
    if not raw or raw.strip() == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_origins(raw: str | None) -> list[str]:
    """
    Parse the ``CORS_ORIGINS`` env value into a list of origins.

    Defaults to an **empty** list, i.e. same-origin only — the template ships
    restrictive by default rather than reflecting every origin. Set
    ``CORS_ORIGINS=https://app.example.com`` to allow a specific frontend.

    Parameters
    ----------
    raw : str or None
        Raw comma-separated origins (e.g., "https://a.com, https://b.com").

    Returns
    -------
    list[str]
        Origin strings, trimmed of whitespace (empty when unset).

    Examples
    --------
    >>> _parse_origins(" https://a.com , https://b.com ")
    ['https://a.com', 'https://b.com']
    >>> _parse_origins(None)
    []
    """
    return _parse_csv(raw, default=[])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Manage application startup and shutdown.

    Enter/Exit Semantics
    --------------------
    - On enter: Initialize dependency injection objects (EchoService, repository, DB).
    - On exit : Dispose/close resources (DB engine).

    Cancellation
    ------------
    - FastAPI/Starlette manage server lifecycle; cooperative cancellation follows ASGI semantics.

    Yields
    ------
    None
        Control returns to FastAPI while the app is running.
    """
    logger.info("API starting up...")
    # Initialize DI BEFORE serving any request to avoid missing state in dependencies.
    app.state.echo_service = EchoService(repo=InMemoryEchoRepository())

    # Initialize the database engine and run migrations. init_db() retries with
    # bounded backoff and RAISES if the database stays unreachable. We do NOT let
    # that crash the process: the pod stays up (liveness stays green) but the
    # readiness probe (/health → 503) keeps it out of the Service rotation until
    # the DB is healthy — so a broken DB tier can never hide behind a green
    # /health or serve 5xx to clients (docs/E2E-FINDINGS.md §5.12). In production
    # migrations normally run in the migration Job / init container before
    # Gunicorn starts; this is the in-process safety net (also used by tests).
    try:
        from your_service_name.backend.db.engine import (  # noqa: PLC0415 — scoped import to isolate DB init failure
            init_db,
        )

        await init_db(app)
    except Exception:
        logger.exception("DB initialization failed. Pod is up but NOT ready (/health → 503).")

    try:
        yield
    finally:
        await close_db(app)
        logger.info("API shutting down...")


def create_app() -> FastAPI:
    """
    Construct and configure the FastAPI application.

    Returns
    -------
    fastapi.FastAPI
        Configured application with CORS, security middleware, and routers.

    Notes
    -----
    - If `CORS_ALLOW_CREDENTIALS=true`, `"*"` is removed from `allow_origins` per browser rules.
      Ensure you provide explicit origins when credentials are enabled.
    """
    app = FastAPI(
        title=os.getenv("API_TITLE", "Backend Title"),
        description=os.getenv("API_DESCRIPTION", "Minimal FastAPI backend"),
        version=API_CONTRACT_VERSION,
        # Interactive Swagger docs (/docs) use FastAPI's built-in page, which loads
        # Swagger UI from the jsDelivr CDN and inlines its init script. A strict CSP
        # would blank it, so the frontend admits that CDN origin + inline script ONLY
        # on /docs via a scoped CSP relaxation (see frontend/middleware.ts). /docs is
        # env-driven so it can be disabled (set API_DOCS_URL="" if desired).
        docs_url=os.getenv("API_DOCS_URL", "/docs"),
        # ReDoc is intentionally left disabled (dropped): it has the same CDN +
        # inline-script CSP problem as /docs, and /docs alone covers interactive docs.
        redoc_url=None,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "Service health checks"},
            {"name": "echo", "description": "Echo demo endpoints"},
            {"name": "items", "description": "Item CRUD sample endpoints"},
        ],
    )

    cors_origins = _parse_origins(os.getenv("CORS_ORIGINS"))
    allow_credentials = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() == "true"
    # If credentials are enabled, do NOT allow "*" (browser restriction).
    if allow_credentials and ("*" in cors_origins):
        cors_origins = [o for o in cors_origins if o != "*"]

    # Methods/headers are env-driven and default to an explicit, non-wildcard set
    # (never ["*"]) so the template does not over-permit cross-origin requests.
    allow_methods = _parse_csv(
        os.getenv("CORS_ALLOW_METHODS"),
        default=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    allow_headers = _parse_csv(
        os.getenv("CORS_ALLOW_HEADERS"),
        default=["Authorization", "Content-Type", "X-Requested-With"],
    )

    app.add_middleware(
        CORSMiddleware,
        # Empty origins => same-origin only (restrictive default).
        allow_origins=cors_origins,
        allow_credentials=allow_credentials,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
    )

    register_rate_limiting(app)
    register_security_middleware(app)

    # Optional request logging (controlled by LOG_REQUESTS env var).
    app.add_middleware(RequestLoggingMiddleware)

    # Correlation id MUST be the outermost middleware so it scopes the id for every log emitted
    # while handling the request — including the request-logging middleware and route handlers.
    # ``add_middleware`` inserts at the front of the stack, so it is added LAST to end up outermost.
    app.add_middleware(CorrelationIdMiddleware)

    # --- Router assembly under a canonical prefix --------------------------------
    app.include_router(build_api_router(prefix="/api", version=_API_VERSION))

    # --- Prometheus metrics instrumentation ------------------------------------
    # Exposes /metrics endpoint for Prometheus scraping
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    # /docs is provided by FastAPI's built-in Swagger page (docs_url above); its
    # /openapi.json is already mounted by the routers, so no extra wiring is needed.

    return app


# ASGI app for uvicorn/gunicorn
app = create_app()
