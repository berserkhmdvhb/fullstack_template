# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
from fastapi import status

if TYPE_CHECKING:
    import httpx


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_health_and_healthz(async_client: httpx.AsyncClient, api_base: str) -> None:
    """
    End-to-end health checks using the in-process ASGI client.
    The response model is HealthStatus(status="ok").
    """
    health_url: Final[str] = f"{api_base}/health"
    res: httpx.Response = await async_client.get(health_url)
    assert res.status_code == status.HTTP_200_OK
    assert res.json() == {"status": "ok"}  # HealthStatus schema enforces "ok" only.

    res2: httpx.Response = await async_client.get(f"{api_base}/healthz")
    assert res2.status_code == status.HTTP_200_OK
    assert res2.json() == {"status": "ok"}  # Same model/shape.
