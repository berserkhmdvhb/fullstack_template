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
from typing import Final

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text

import your_service_name.backend.db.engine as _engine_module
from your_service_name.backend.db import schema
from your_service_name.backend.db.engine import get_session
from your_service_name.backend.paths import HEALTH_ROUTE, LIVENESS_ROUTE
from your_service_name.backend.schemas.health import HealthStatus, ReadinessReason, ReadinessStatus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


_REASONS: Final[dict[schema.SchemaState, ReadinessReason]] = {
    schema.SchemaState.MISSING: "schema_missing",
    schema.SchemaState.BEHIND: "schema_behind",
}
_VERSION_TABLE: Final[str] = "alembic_version"


def _degraded_response(reason: ReadinessReason) -> JSONResponse:
    """Readiness payload for when the service is not ready (HTTP 503), with the reason."""
    logger.warning("Readiness check failed (%s); reporting not-ready (503).", reason)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ReadinessStatus(status="degraded", reason=reason).model_dump(),
    )


async def _schema_state(revisions: schema.Revisions) -> schema.SchemaState:
    """Read-only: the database's revision against the one this code expects."""
    async for session in get_session():
        connection = await session.connection()
        if not await connection.run_sync(lambda c: inspect(c).has_table(_VERSION_TABLE)):
            return schema.SchemaState.MISSING
        rows = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalars()
        return schema.classify(next(iter(rows), None), revisions)
    return schema.SchemaState.MISSING  # pragma: no cover - get_session always yields


@router.get(HEALTH_ROUTE)
async def health_check() -> JSONResponse:
    """
    Readiness probe.

    Returns HTTP 200 ``{"status": "ok"}`` when the database is reachable and its schema revision is
    this release's head, or one this release does not know (a newer release migrated first; ready,
    because migrations stay compatible with the previous release). Returns HTTP 503
    ``{"status": "degraded", "reason": ...}`` otherwise, with ``reason`` one of
    ``database_unreachable``, ``schema_missing`` (no version table or no row) or ``schema_behind``
    (an older, known revision). The body never names a revision, a URL or an error.

    The check is read-only. The status code IS the contract: Kubernetes' readinessProbe keys off it,
    so a Pod that cannot serve is removed from the Service endpoints, and a rolling update keeps the
    old Pods serving until the migration Job has brought the schema to the new head. Liveness is
    separate (/healthz) and never touches the database or the schema, so a Pod is never restarted
    for waiting on a migration.
    """
    revisions = _engine_module._revisions  # noqa: SLF001 — read once at startup by init_db
    if not _engine_module._db_initialized or revisions is None:  # noqa: SLF001 — init flag
        return _degraded_response("database_unreachable")

    try:
        state = await _schema_state(revisions)
    except Exception:  # noqa: BLE001 — intentional: any DB error means not ready.
        return _degraded_response("database_unreachable")

    reason = _REASONS.get(state)
    if reason is not None:
        return _degraded_response(reason)
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
