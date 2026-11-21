# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Deprecation and Sunset response headers for a retiring API (see wiki/api-lifecycle.md).

Responsibilities:
- Build the ``Deprecation`` header (RFC 9745: a structured-field date, ``@<unix-seconds>``).
- Build the ``Sunset`` header (RFC 8594: an HTTP-date, always in GMT).
- Build ``Link`` values with ``rel="deprecation"`` and ``rel="sunset"``.

Public API:
- `deprecation_headers`: the headers as a dict (pure; easy to test).
- `mark_deprecated`: set them on a FastAPI ``Response``.

Usage
-----
@router.get("/old")
async def old(response: Response) -> Thing:
    mark_deprecated(response, deprecated_at=..., sunset=..., deprecation_link="https://...")
    ...

Notes
-----
- Dates must be timezone-aware; they are written in UTC.
- The sunset must not be earlier than the deprecation date (RFC 9745, section 4).
- Deprecating a route changes only these headers, never its behavior.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Response

_BAD_LINK_CHARS = frozenset(' <>"')


def _utc(moment: datetime, name: str) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        msg = f"{name} must have a time zone"
        raise ValueError(msg)
    return moment.astimezone(UTC)


def _link(url: str, rel: str) -> str:
    """A ``Link`` value; a URL that could break out of the header is refused."""
    if not url or not url.isprintable() or _BAD_LINK_CHARS & set(url):
        msg = f"the {rel} link must be a URL without spaces, control characters, <, > or quotes"
        raise ValueError(msg)
    return f'<{url}>; rel="{rel}"'


def deprecation_headers(
    *,
    deprecated_at: datetime | None = None,
    sunset: datetime | None = None,
    deprecation_link: str | None = None,
    sunset_link: str | None = None,
) -> dict[str, str]:
    """
    The headers announcing a deprecation and/or a sunset.

    Parameters
    ----------
    deprecated_at : datetime, optional
        When the resource was (or will be) deprecated: ``Deprecation: @1688169599``.
    sunset : datetime, optional
        When it is expected to become unresponsive: ``Sunset: Sun, 30 Jun 2024 23:59:59 GMT``.
    deprecation_link, sunset_link : str, optional
        URLs of the deprecation notice and the sunset policy, sent as one ``Link`` header.

    Raises
    ------
    ValueError
        A naive datetime, a sunset earlier than the deprecation date, or an unusable link.
    """
    headers: dict[str, str] = {}
    start = _utc(deprecated_at, "deprecated_at") if deprecated_at else None
    end = _utc(sunset, "sunset") if sunset else None
    if start and end and end < start:
        msg = "sunset must not be earlier than deprecated_at"
        raise ValueError(msg)
    if start:
        headers["Deprecation"] = f"@{int(start.timestamp())}"
    if end:
        headers["Sunset"] = format_datetime(end, usegmt=True)
    links = []
    if deprecation_link is not None:
        links.append(_link(deprecation_link, "deprecation"))
    if sunset_link is not None:
        links.append(_link(sunset_link, "sunset"))
    if links:
        headers["Link"] = ", ".join(links)
    return headers


def mark_deprecated(
    response: Response,
    *,
    deprecated_at: datetime | None = None,
    sunset: datetime | None = None,
    deprecation_link: str | None = None,
    sunset_link: str | None = None,
) -> None:
    """Set the deprecation headers on ``response``; a ``Link`` already set is kept and extended."""
    for name, value in deprecation_headers(
        deprecated_at=deprecated_at,
        sunset=sunset,
        deprecation_link=deprecation_link,
        sunset_link=sunset_link,
    ).items():
        if name == "Link" and "Link" in response.headers:
            value = f"{response.headers['Link']}, {value}"  # noqa: PLW2901
        response.headers[name] = value
