# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Health endpoints.

Responsibilities:
- Provide liveness/readiness checks for monitoring.

Public API:
- `router`: APIRouter exposing `/health` and `/healthz`.

Operational:
- Concurrency: Async handlers; no shared state.
- Logging: None (can be added if needed).

Usage
-----
from fastapi import FastAPI
from your_service_name.backend.api.routers.v1.health import router

app = FastAPI()
app.include_router(router)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

import your_service_name.backend.db.engine as _engine_module
from your_service_name.backend.db.engine import get_session
from your_service_name.backend.paths import HEALTH_ROUTE, LIVENESS_ROUTE
from your_service_name.backend.schemas.health import HealthStatus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


def _degraded_response() -> JSONResponse:
    """Readiness payload for when the database tier is unavailable (HTTP 503)."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=HealthStatus(status="degraded").model_dump(),
    )


@router.get(HEALTH_ROUTE)
async def health_check() -> JSONResponse:
    """
    Readiness probe.

    Returns HTTP 200 ``{"status": "ok"}`` only when the database engine is
    initialized AND a live ``SELECT 1`` succeeds. Returns HTTP 503
    ``{"status": "degraded"}`` otherwise.

    The status code IS the contract: Kubernetes' readinessProbe keys off it, so
    a pod whose DB tier is down is removed from the Service endpoints instead of
    silently serving 5xx to clients — the failure mode this hardening closes
    (seen in an end-to-end run). Because the probe also runs a live query,
    a transient post-startup DB blip recovers on the next successful check
    without a restart. Liveness is separate (/healthz) and must never depend on
    the DB, so a database outage degrades readiness without restart-looping.
    """
    if not _engine_module._db_initialized:  # noqa: SLF001 — intentional: read module-level init flag
        logger.warning("Database not initialized; reporting not-ready (503).")
        return _degraded_response()

    try:
        async for session in get_session():
            await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 — intentional: any DB error means not ready.
        logger.warning("Database readiness check failed; reporting not-ready (503).")
        return _degraded_response()

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=HealthStatus(status="ok").model_dump(),
    )


@router.get(LIVENESS_ROUTE, status_code=status.HTTP_200_OK, response_model=HealthStatus)
async def healthz() -> HealthStatus:
    """
    Return a bare health status for uptime/proxy monitors (e.g., a Kubernetes
    liveness probe or the NGINX Ingress Controller health check).

    Returns
    -------
    HealthStatus
        A payload containing `status="ok"`.

    Notes
    -----
    - This endpoint is intentionally minimal for fast checks by load balancers.
    """
    return HealthStatus(status="ok")
