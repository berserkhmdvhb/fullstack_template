# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import status

if TYPE_CHECKING:
    import httpx


@pytest.mark.integration
@pytest.mark.asyncio
async def test_lifespan_initializes_echo_service(
    async_client: httpx.AsyncClient, api_base: str
) -> None:
    """
    Using create_app() means lifespan runs and seeds app.state.echo_service,
    so DI-backed endpoints work without raising.
    """
    res: httpx.Response = await async_client.post(f"{api_base}/echo", json={"text": "abc"})
    assert res.status_code == status.HTTP_201_CREATED  # Lifespan seeds service before serving.
