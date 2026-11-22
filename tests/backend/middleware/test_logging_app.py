# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The request log through the real app: what it skips, what it holds, and how it escapes."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
from fastapi.testclient import TestClient

from your_service_name.backend import paths
from your_service_name.backend.logging.correlation import RequestIdFilter
from your_service_name.backend.logging.json_formatter import JsonLogFormatter
from your_service_name.backend.main import create_app
from your_service_name.backend.middleware import logging as request_logging
from your_service_name.backend.settings import Settings

TEXT = "%(levelname)s %(message)s"
TOKENS = ("zq-query", "zq-bearer", "zq-cookie", "zq-ident", "zq-agent")
# The formatter's own fields, plus three the JSON formatter copies from every stdlib record.
KEYS = {"@timestamp", "level", "logger", "message", "request_id"}
KEYS |= {"msecs", "relativeCreated", "taskName"}


class _Lines(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.addFilter(RequestIdFilter())
        self.json: list[str] = []
        self.text: list[str] = []
        self._json_fmt = JsonLogFormatter()
        self._text_fmt = logging.Formatter(TEXT)

    def emit(self, record: logging.LogRecord) -> None:
        self.json.append(self._json_fmt.format(record))
        self.text.append(self._text_fmt.format(record))


@pytest.fixture
def lines() -> Iterator[_Lines]:
    handler = _Lines()
    log = request_logging.logger
    old = (log.level, log.disabled)
    log.setLevel(logging.DEBUG)
    log.disabled = False
    log.addHandler(handler)
    yield handler
    log.removeHandler(handler)
    log.setLevel(old[0])
    log.disabled = old[1]


def _client() -> TestClient:
    return TestClient(create_app(Settings(LOG_REQUESTS=True)))


def test_probes_write_no_request_line(lines: _Lines) -> None:
    client = _client()
    for path in sorted(paths.exempt_paths(Settings().API_VERSION)):
        client.get(path)
    assert lines.json == []
    client.get("/api/v1/nosuch")
    assert len(lines.json) == 1


def test_a_request_line_holds_only_the_known_keys_and_no_token(lines: _Lines) -> None:
    res = _client().get(
        f"/api/v1/nosuch?token={TOKENS[0]}",
        headers={
            "Authorization": f"Bearer {TOKENS[1]}",
            "Cookie": f"sid={TOKENS[2]}",
            "X-Authenticated-User-Id": TOKENS[3],
            "User-Agent": TOKENS[4],
        },
    )
    assert res.status_code == 404  # noqa: PLR2004
    assert len(lines.json) == 1
    record = json.loads(lines.json[0])
    assert set(record) == KEYS
    assert record["message"].startswith("GET /api/v1/nosuch 404 ")
    for token in TOKENS:
        assert token not in lines.json[0]
        assert token not in lines.text[0]


def test_control_characters_in_the_path_cannot_forge_a_line(lines: _Lines) -> None:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message: MutableMapping[str, Any]) -> None:
        return None

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/v1/x\nINFO forged\r\nline\x1b[2J",  # decoded, as a server hands it over
        "raw_path": b"/api/v1/x%0AINFO%20forged%0D%0Aline",
        "query_string": b"",
        "headers": [],
        "client": ("203.0.113.5", 4242),
        "server": ("testserver", 80),
    }
    asyncio.run(create_app(Settings(LOG_REQUESTS=True))(scope, receive, send))
    assert len(lines.text) == 1
    assert len(lines.text[0].splitlines()) == 1
    # CR and LF are already dropped when Starlette parses the URL; the rest are escaped here.
    assert "\\x1b[2J" in lines.text[0]
    assert "\x1b" not in lines.text[0]
