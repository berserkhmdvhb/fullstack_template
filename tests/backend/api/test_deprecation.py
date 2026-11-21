# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The Deprecation and Sunset header helper: exact header formats (RFC 9745, RFC 8594)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI, Response

from your_service_name.backend.api.deprecation import deprecation_headers, mark_deprecated

# The RFC 9745 example: deprecated Friday 30 June 2023 23:59:59 UTC, sunset Sunday 30 June 2024.
DEPRECATED = datetime(2023, 6, 30, 23, 59, 59, tzinfo=UTC)
SUNSET = datetime(2024, 6, 30, 23, 59, 59, tzinfo=UTC)
DEPRECATION_VALUE = "@1688169599"
SUNSET_VALUE = "Sun, 30 Jun 2024 23:59:59 GMT"
DOC = "https://developer.example.com/deprecation"
POLICY = "https://developer.example.com/sunset"


def test_deprecation_is_a_structured_field_date() -> None:
    assert deprecation_headers(deprecated_at=DEPRECATED) == {"Deprecation": DEPRECATION_VALUE}


def test_sunset_is_an_http_date() -> None:
    assert deprecation_headers(sunset=SUNSET) == {"Sunset": SUNSET_VALUE}


def test_both_headers() -> None:
    headers = deprecation_headers(deprecated_at=DEPRECATED, sunset=SUNSET)
    assert headers == {"Deprecation": DEPRECATION_VALUE, "Sunset": SUNSET_VALUE}


def test_a_date_in_another_time_zone_is_written_in_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    moment = datetime(2024, 7, 1, 1, 59, 59, tzinfo=plus_two)
    assert deprecation_headers(sunset=moment) == {"Sunset": SUNSET_VALUE}
    assert deprecation_headers(deprecated_at=moment) == {"Deprecation": "@1719791999"}


def test_links_use_the_deprecation_and_sunset_relations() -> None:
    headers = deprecation_headers(deprecation_link=DOC, sunset_link=POLICY)
    assert headers == {"Link": f'<{DOC}>; rel="deprecation", <{POLICY}>; rel="sunset"'}


def test_a_deprecation_link_alone() -> None:
    assert deprecation_headers(deprecated_at=DEPRECATED, deprecation_link=DOC) == {
        "Deprecation": DEPRECATION_VALUE,
        "Link": f'<{DOC}>; rel="deprecation"',
    }


def test_nothing_set_means_no_headers() -> None:
    assert deprecation_headers() == {}


def test_a_naive_datetime_is_refused() -> None:
    with pytest.raises(ValueError, match="time zone"):
        deprecation_headers(deprecated_at=datetime(2023, 6, 30, 23, 59, 59))  # noqa: DTZ001


def test_a_sunset_before_the_deprecation_is_refused() -> None:
    """RFC 9745, section 4: the sunset must not be earlier than the deprecation date."""
    with pytest.raises(ValueError, match="earlier"):
        deprecation_headers(deprecated_at=SUNSET, sunset=DEPRECATED)


@pytest.mark.parametrize(
    "url", ["https://x.example/a>b", 'https://x.example/"', "https://x.example/\r\nX: y", ""]
)
def test_a_link_that_could_break_the_header_is_refused(url: str) -> None:
    with pytest.raises(ValueError, match="link"):
        deprecation_headers(deprecation_link=url)


async def test_a_route_marked_deprecated_sends_the_headers() -> None:
    """A test-only route: no real route is deprecated."""
    app = FastAPI()

    @app.get("/old")
    async def old(response: Response) -> dict[str, str]:
        mark_deprecated(
            response,
            deprecated_at=DEPRECATED,
            sunset=SUNSET,
            deprecation_link=DOC,
            sunset_link=POLICY,
        )
        return {"ok": "yes"}

    @app.get("/new")
    async def new() -> dict[str, str]:
        return {"ok": "yes"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        res = await c.get("/old")
        assert res.status_code == httpx.codes.OK
        assert res.headers["Deprecation"] == DEPRECATION_VALUE
        assert res.headers["Sunset"] == SUNSET_VALUE
        assert res.headers["Link"] == f'<{DOC}>; rel="deprecation", <{POLICY}>; rel="sunset"'
        assert res.json() == {"ok": "yes"}
        other = await c.get("/new")
        assert "Deprecation" not in other.headers
        assert "Sunset" not in other.headers


async def test_an_existing_link_header_is_extended() -> None:
    response = Response()
    response.headers["Link"] = '<https://x.example/next>; rel="next"'
    mark_deprecated(response, deprecation_link=DOC)
    assert (
        response.headers["Link"]
        == f'<https://x.example/next>; rel="next", <{DOC}>; rel="deprecation"'
    )
