# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The opt-in CSP report endpoint: formats, limits, problems, and what reaches the log."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from your_service_name.backend.api.routers import csp_report
from your_service_name.backend.main import create_app
from your_service_name.backend.services.csp_report_service import (
    MAX_FIELD_LENGTH,
    MAX_REPORTS_PER_REQUEST,
)
from your_service_name.backend.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

PATH = "/api/v1/csp-report"
REPORT_URI = "application/csp-report"
REPORT_TO = "application/reports+json"
LOGGER = csp_report.__name__
SECRET = "tok-fake-not-real"  # noqa: S105


def _app(**extra: object) -> FastAPI:
    return create_app(Settings(CSP_REPORT_ENABLED=True, **extra))  # type: ignore[arg-type]


async def _post(
    app: FastAPI,
    content: bytes | str | AsyncIterator[bytes],
    content_type: str | None = REPORT_URI,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    all_headers = dict(headers or {})
    if content_type is not None:
        all_headers["Content-Type"] = content_type
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.post(PATH, content=content, headers=all_headers)


def _legacy(**fields: object) -> bytes:
    return json.dumps({"csp-report": fields}).encode()


def _violations(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]


def _fields(record: logging.LogRecord) -> dict[str, Any]:
    return record.csp_violation  # type: ignore[attr-defined,no-any-return]


@pytest.fixture(autouse=True)
def _capture(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    logging.getLogger(LOGGER).disabled = False


class TestMounting:
    async def test_off_by_default_is_a_404_problem(self) -> None:
        response = await _post(create_app(Settings()), _legacy())
        assert response.status_code == httpx.codes.NOT_FOUND
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_never_in_the_openapi_document(self) -> None:
        schema = _app().openapi()
        assert not [p for p in schema["paths"] if "csp" in p]
        assert "csp" not in json.dumps(schema).lower()


class TestFormats:
    async def test_report_uri_format(self, caplog: pytest.LogCaptureFixture) -> None:
        body = _legacy(
            **{
                "document-uri": "https://app.example/page",
                "blocked-uri": "https://evil.example/x.js",
                "violated-directive": "script-src",
                "effective-directive": "script-src-elem",
                "disposition": "enforce",
                "status-code": 200,
                "source-file": "https://app.example/app.js",
                "line-number": 12,
                "column-number": 7,
            }
        )
        response = await _post(_app(), body)
        assert response.status_code == httpx.codes.NO_CONTENT
        assert response.content == b""
        [record] = _violations(caplog)
        assert _fields(record) == {
            "document-uri": "https://app.example/page",
            "blocked-uri": "https://evil.example/x.js",
            "violated-directive": "script-src",
            "effective-directive": "script-src-elem",
            "disposition": "enforce",
            "status-code": 200,
            "source-file": "https://app.example/app.js",
            "line-number": 12,
            "column-number": 7,
        }

    async def test_reports_json_format_takes_only_csp_violations(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        body = json.dumps(
            [
                {"type": "deprecation", "body": {"id": "x"}},
                {
                    "type": "csp-violation",
                    "url": "https://app.example/",
                    "body": {
                        "documentURL": "https://app.example/p",
                        "blockedURL": "inline",
                        "effectiveDirective": "script-src",
                        "disposition": "report",
                        "statusCode": 200,
                        "lineNumber": 3,
                        "columnNumber": 4,
                        "sourceFile": "https://app.example/a.js",
                    },
                },
                "not-an-object",
            ]
        )
        response = await _post(_app(), body, REPORT_TO)
        assert response.status_code == httpx.codes.NO_CONTENT
        [record] = _violations(caplog)
        assert _fields(record) == {
            "document-uri": "https://app.example/p",
            "blocked-uri": "inline",
            "effective-directive": "script-src",
            "disposition": "report",
            "status-code": 200,
            "line-number": 3,
            "column-number": 4,
            "source-file": "https://app.example/a.js",
        }

    async def test_content_type_parameters_are_allowed(self) -> None:
        response = await _post(_app(), _legacy(), f"{REPORT_URI}; charset=utf-8")
        assert response.status_code == httpx.codes.NO_CONTENT

    @pytest.mark.parametrize("content_type", ["application/json", "text/plain", None])
    async def test_any_other_content_type_is_415(self, content_type: str | None) -> None:
        response = await _post(_app(), _legacy(), content_type)
        assert response.status_code == httpx.codes.UNSUPPORTED_MEDIA_TYPE
        assert response.headers["content-type"].startswith("application/problem+json")

    @pytest.mark.parametrize(
        ("body", "content_type"),
        [
            (b"{not json", REPORT_URI),
            (b"\xff\xfe", REPORT_URI),
            (b'{"other": 1}', REPORT_URI),
            (b'{"csp-report": [1]}', REPORT_URI),
            (b'{"csp-report": {}}', REPORT_TO),  # an object, not an array
            (b"", REPORT_URI),
        ],
    )
    async def test_malformed_bodies_are_400_problems(self, body: bytes, content_type: str) -> None:
        response = await _post(_app(), body, content_type)
        assert response.status_code == httpx.codes.BAD_REQUEST
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_at_most_n_reports_per_request(self, caplog: pytest.LogCaptureFixture) -> None:
        reports = [
            {"type": "csp-violation", "body": {"blockedURL": f"https://e/{i}"}} for i in range(25)
        ]
        response = await _post(_app(), json.dumps(reports), REPORT_TO)
        assert response.status_code == httpx.codes.NO_CONTENT
        assert len(_violations(caplog)) == MAX_REPORTS_PER_REQUEST == 10  # noqa: PLR2004


class TestSizeCap:
    async def test_a_declared_length_over_the_cap_is_413(self) -> None:
        app = _app(CSP_REPORT_MAX_BYTES=100)
        response = await _post(app, b" " * 101)
        assert response.status_code == httpx.codes.REQUEST_ENTITY_TOO_LARGE
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_exactly_the_cap_is_read(self) -> None:
        body = _legacy(**{"blocked-uri": "x"})
        app = _app(CSP_REPORT_MAX_BYTES=len(body))
        assert (await _post(app, body)).status_code == httpx.codes.NO_CONTENT

    async def test_a_stream_without_length_stops_being_read_at_the_cap(self) -> None:
        app = _app(CSP_REPORT_MAX_BYTES=1000)
        sent = 0

        async def chunks() -> AsyncIterator[bytes]:
            nonlocal sent
            for _ in range(1000):
                sent += 1
                yield b"x" * 100

        response = await _post(app, chunks())
        assert response.status_code == httpx.codes.REQUEST_ENTITY_TOO_LARGE
        assert sent < 50  # noqa: PLR2004 — nowhere near the 1000 chunks offered


class TestRateLimit:
    async def test_over_the_limit_is_a_429_problem_with_retry_after(self) -> None:
        app = _app(CSP_REPORT_RATE_LIMIT="2/minute")
        assert [(await _post(app, _legacy())).status_code for _ in range(2)] == [204, 204]
        response = await _post(app, _legacy())
        assert response.status_code == httpx.codes.TOO_MANY_REQUESTS
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.headers["retry-after"] == "60"

    async def test_it_does_not_use_the_default_limit_counter(self) -> None:
        app = _app(CSP_REPORT_RATE_LIMIT="1/minute", RATE_LIMIT_DEFAULT="1000/minute")
        await _post(app, _legacy())
        assert (await _post(app, _legacy())).status_code == httpx.codes.TOO_MANY_REQUESTS
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            assert (await client.get("/api/v1/healthz")).status_code == httpx.codes.OK

    async def test_a_limited_request_is_not_read_or_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        app = _app(CSP_REPORT_RATE_LIMIT="1/minute")
        await _post(app, _legacy(**{"blocked-uri": "a"}))
        caplog.clear()
        await _post(app, _legacy(**{"blocked-uri": "b"}))
        assert _violations(caplog) == []


class TestSanitising:
    async def _one(self, caplog: pytest.LogCaptureFixture, **fields: object) -> dict[str, Any]:
        response = await _post(_app(), _legacy(**fields))
        assert response.status_code == httpx.codes.NO_CONTENT
        [record] = _violations(caplog)
        return _fields(record)

    async def test_query_and_fragment_are_stripped_from_urls(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        got = await self._one(
            caplog,
            **{
                "document-uri": f"https://a.example/p?token={SECRET}#frag",
                "blocked-uri": f"https://b.example/x.js?k={SECRET}",
                "source-file": f"https://a.example/a.js#{SECRET}",
            },
        )
        assert got == {
            "document-uri": "https://a.example/p",
            "blocked-uri": "https://b.example/x.js",
            "source-file": "https://a.example/a.js",
        }
        assert SECRET not in caplog.text

    async def test_control_characters_are_removed(self, caplog: pytest.LogCaptureFixture) -> None:
        got = await self._one(
            caplog,
            **{
                "violated-directive": (
                    "script-src\nERROR forged line\r\x1b[31m\x00\N{LINE SEPARATOR}x"
                ),
                "blocked-uri": "https://a.example/\tb",
            },
        )
        assert got["violated-directive"] == "script-srcERROR forged line[31mx"
        assert got["blocked-uri"] == "https://a.example/b"
        assert all(ch.isprintable() for value in got.values() for ch in str(value))
        message = _violations(caplog)[0].getMessage()
        assert "\n" not in message
        assert "\r" not in message

    async def test_values_are_truncated(self, caplog: pytest.LogCaptureFixture) -> None:
        got = await self._one(caplog, **{"violated-directive": "d" * 5000})
        assert got["violated-directive"] == "d" * MAX_FIELD_LENGTH
        assert MAX_FIELD_LENGTH == 512  # noqa: PLR2004

    async def test_unknown_fields_are_dropped(self, caplog: pytest.LogCaptureFixture) -> None:
        got = await self._one(
            caplog,
            **{
                "blocked-uri": "inline",
                "original-policy": "default-src 'self'; report-uri /x",
                "script-sample": SECRET,
                "referrer": "https://r.example/",
                "evil": "x",
            },
        )
        assert got == {"blocked-uri": "inline"}
        assert SECRET not in caplog.text

    async def test_only_strings_and_ints_are_kept(self, caplog: pytest.LogCaptureFixture) -> None:
        got = await self._one(
            caplog,
            **{
                "blocked-uri": {"a": 1},
                "violated-directive": ["x"],
                "effective-directive": None,
                "disposition": True,
                "status-code": 200.5,
                "line-number": "12",
                "column-number": 2**40,
                "source-file": "ok",
            },
        )
        assert got == {"source-file": "ok"}

    async def test_a_report_with_no_known_field_is_not_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        response = await _post(_app(), _legacy(evil="x"))
        assert response.status_code == httpx.codes.NO_CONTENT
        assert _violations(caplog) == []


def test_the_path_comes_from_paths() -> None:
    from your_service_name.backend import paths  # noqa: PLC0415

    assert f"/api/v1{paths.CSP_REPORT_ROUTE}" == PATH
