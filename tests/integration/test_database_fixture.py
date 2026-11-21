# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The shared fixtures pick the database: PostgreSQL if the environment asks for it, else SQLite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import POSTGRES_TEST_URL
from your_service_name.backend.settings import Settings

if TYPE_CHECKING:
    import httpx

pytestmark = pytest.mark.integration

CREATED = 201


def test_the_selected_database_matches_the_environment() -> None:
    url = Settings().database_url or ""
    if POSTGRES_TEST_URL:
        assert url == POSTGRES_TEST_URL
    else:
        assert url.startswith("sqlite")


async def _create_one_and_count(client: httpx.AsyncClient, api_base: str) -> int:
    res = await client.post(f"{api_base}/items", json={"name": "leak-check"})
    assert res.status_code == CREATED
    return int((await client.get(f"{api_base}/items")).json()["total"])


@pytest.mark.postgres_only
async def test_isolation_a(async_client: httpx.AsyncClient, api_base: str) -> None:
    assert await _create_one_and_count(async_client, api_base) == 1


@pytest.mark.postgres_only
async def test_isolation_b(async_client: httpx.AsyncClient, api_base: str) -> None:
    """Whichever of the two runs second still sees exactly its own row."""
    assert await _create_one_and_count(async_client, api_base) == 1
