# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for centralized DI accessors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from your_service_name.backend.api.accessors import (
    MSG_ERROR_ECHO_SERVICE_NOT_INITIALIZED,
    get_echo_service,
)
from your_service_name.backend.main import create_app
from your_service_name.backend.services.echo_service import EchoService

if TYPE_CHECKING:
    from fastapi import FastAPI


def _make_request(app: FastAPI) -> Request:
    """Construct a Starlette Request bound to `app` without real I/O."""
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": app,
    }
    return Request(scope)


def test_get_echo_service_success_when_lifespan_ran() -> None:
    """Accessor returns EchoService after lifespan has initialized app.state."""
    app = create_app()
    with TestClient(app):
        req = _make_request(app)
        svc = get_echo_service(req)
        assert isinstance(svc, EchoService)


def test_get_echo_service_raises_when_not_initialized() -> None:
    """Accessor raises RuntimeError when app.state lacks echo_service."""
    app = create_app()
    req = _make_request(app)

    with pytest.raises(RuntimeError) as ei:
        _ = get_echo_service(req)

    assert MSG_ERROR_ECHO_SERVICE_NOT_INITIALIZED in str(ei.value)
