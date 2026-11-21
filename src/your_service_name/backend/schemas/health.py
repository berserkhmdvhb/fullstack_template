# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Health probe schema used by monitoring endpoints.

Responsibilities:
- Provide a minimal, stable shape for health responses.

Public API:
- `HealthStatus`: Pydantic model with a `status` field.
- `ReadinessStatus`: `HealthStatus` plus the `reason` a readiness probe is not ready.

Operational:
- Concurrency: Safe; pure data container.
- Logging: None.

Usage
-----
from your_service_name.backend.schemas.health import HealthStatus
payload = HealthStatus(status="ok")
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthStatus(BaseModel):
    """
    Health probe payload indicating service liveness/readiness state.

    Attributes
    ----------
    status : Literal["ok"]
        Health status string; "ok" on success.

    Examples
    --------
    >>> HealthStatus(status="ok").model_dump()
    {'status': 'ok'}
    """

    status: Literal["ok", "degraded"] = Field(..., description='Health status: "ok" or "degraded".')

    model_config = {
        "extra": "forbid",  # Ensure health responses have no extra fields.
    }


ReadinessReason = Literal["database_unreachable", "schema_missing", "schema_behind"]


class ReadinessStatus(HealthStatus):
    """
    Readiness payload: `HealthStatus` plus, when not ready, why (never a detail of the failure).

    Attributes
    ----------
    reason : str or None
        ``database_unreachable``, ``schema_missing`` or ``schema_behind``; absent when ready.
    """

    reason: ReadinessReason | None = Field(
        default=None, description="Why the service is not ready; absent when it is."
    )


# Ensure model is fully defined for Pydantic v2/OpenAPI generation.
HealthStatus.model_rebuild()
ReadinessStatus.model_rebuild()
