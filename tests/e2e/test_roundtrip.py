# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pytest
from fastapi import status

if TYPE_CHECKING:
    import httpx


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_echo_create_and_history(async_client: httpx.AsyncClient, api_base: str) -> None:
    """
    POST /echo returns 201 + Location header; GET /echo/history echoes back items.
    Shapes are enforced by EchoRequest/EchoResponse schemas.
    """
    payload = {"text": "hello world"}
    post_url: Final[str] = f"{api_base}/echo"
    res: httpx.Response = await async_client.post(post_url, json=payload)

    assert res.status_code == status.HTTP_201_CREATED
    expected_location: Final[str] = f"{api_base}/echo/history"
    assert res.headers.get("Location") == expected_location  # Router sets this.

    body = res.json()
    assert body["text"] == payload["text"]
    assert body["length"] == len(payload["text"])  # Response schema fields.

    # History should include the new item
    history_url: Final[str] = expected_location
    res2: httpx.Response = await async_client.get(history_url)
    assert res2.status_code == status.HTTP_200_OK
    items = res2.json()
    assert any(
        it.get("text") == payload["text"] and it.get("length") == len(payload["text"])
        for it in items
    )  # Built from repo tuples -> EchoResponse list.
