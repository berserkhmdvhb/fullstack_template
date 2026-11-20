# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import status

if TYPE_CHECKING:
    import httpx


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_echo_rejects_empty_text(async_client: httpx.AsyncClient, api_base: str) -> None:
    """
    EchoRequest.text has min_length=1; empty string should 422.
    """
    res: httpx.Response = await async_client.post(f"{api_base}/echo", json={"text": ""})
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY  # Enforced by schema.


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_echo_rejects_extra_fields(async_client: httpx.AsyncClient, api_base: str) -> None:
    """
    EchoRequest sets extra="forbid"; unexpected fields should 422.
    """
    res: httpx.Response = await async_client.post(
        f"{api_base}/echo", json={"text": "ok", "unexpected": True}
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY  # extra="forbid".
