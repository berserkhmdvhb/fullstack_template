# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for RequestLoggingMiddleware.

Covers:
- log_requests=False (the default): passes through without logging
- log_requests=True: logs method, path, status, duration
- Skips paths in _SKIP_PATHS (/healthz)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import Address
from starlette.requests import Request
from starlette.responses import Response

from your_service_name.backend.middleware.logging import RequestLoggingMiddleware

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


EXPECTED_STATUS_CODE: int = 200


@pytest.fixture
def mock_call_next() -> AsyncMock:
    """Create a mock call_next function."""
    mock_response = Response(status_code=EXPECTED_STATUS_CODE, content=b"OK")
    return AsyncMock(return_value=mock_response)


@pytest.fixture
def middleware() -> RequestLoggingMiddleware:
    """Create a RequestLoggingMiddleware with request logging on."""
    # RequestLoggingMiddleware requires an app argument
    mock_app = MagicMock()
    return RequestLoggingMiddleware(app=mock_app, log_requests=True)


class TestLoggingDisabled:
    """Test middleware behavior when logging is disabled."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("middleware")
    async def test_passes_through_without_logging(
        self,
        mock_call_next: AsyncMock,
    ) -> None:
        """Should pass through without logging when log_requests is off (the default)."""
        mock_app = MagicMock()
        middleware_disabled = RequestLoggingMiddleware(app=mock_app)

        mock_request = MagicMock(spec=Request)
        mock_request.url.path = "/api/test"

        response = await middleware_disabled.dispatch(mock_request, mock_call_next)

        mock_call_next.assert_called_once_with(mock_request)
        assert response.status_code == EXPECTED_STATUS_CODE


class TestLoggingEnabled:
    """Test middleware behavior when logging is enabled."""

    @pytest.mark.asyncio
    async def test_logs_request(
        self,
        middleware: RequestLoggingMiddleware,
        mock_call_next: AsyncMock,
    ) -> None:
        """Should log request when log_requests is on."""
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/api/test"
        mock_request.client = Address("203.0.113.5", 4242)

        with patch("your_service_name.backend.middleware.logging.logger") as mock_logger:
            response = await middleware.dispatch(mock_request, mock_call_next)

            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0]

            # Check log format: METHOD /path STATUS duration client=IP
            assert call_args[0] == "%s %s %s %.3fs client=%s"
            assert call_args[5] == "203.0.113.5"
            assert call_args[1] == "GET"
            assert call_args[2] == "/api/test"
            assert call_args[3] == EXPECTED_STATUS_CODE

            assert response.status_code == EXPECTED_STATUS_CODE


class TestSkipPaths:
    """Test middleware skips the paths it is given."""

    @pytest.mark.asyncio
    async def test_skips_a_listed_path(self, mock_call_next: AsyncMock) -> None:
        """Should not log a path in skip_paths, and still call the next handler."""
        middleware = RequestLoggingMiddleware(
            MagicMock(), log_requests=True, skip_paths=frozenset({"/probe"})
        )
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/probe"

        with patch("your_service_name.backend.middleware.logging.logger") as mock_logger:
            response = await middleware.dispatch(mock_request, mock_call_next)

        mock_logger.info.assert_not_called()
        mock_call_next.assert_called_once_with(mock_request)
        assert response.status_code == EXPECTED_STATUS_CODE


class TestNothingSensitiveIsLogged:
    """The request line holds method, path, status, duration and the resolved client only."""

    @pytest.mark.asyncio
    async def test_a_query_value_and_headers_never_reach_the_line(
        self, middleware: RequestLoggingMiddleware, mock_call_next: AsyncMock
    ) -> None:
        secrets = ("zq-query-tok", "zq-bearer-tok", "zq-cookie-tok", "zq-ident-tok", "zq-fwd-tok")
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/test",
            "query_string": b"token=" + secrets[0].encode(),
            "client": ("203.0.113.5", 4242),
            "headers": [
                (b"authorization", b"Bearer " + secrets[1].encode()),
                (b"cookie", b"sid=" + secrets[2].encode()),
                (b"x-auth-request-user", secrets[3].encode()),
                (b"x-forwarded-for", secrets[4].encode()),
            ],
        }
        with patch("your_service_name.backend.middleware.logging.logger") as mock_logger:
            await middleware.dispatch(Request(scope), mock_call_next)
        args = mock_logger.info.call_args[0]
        line = args[0] % args[1:]
        assert line.startswith("GET /api/test 200 ")
        assert line.endswith("client=203.0.113.5")
        for secret in secrets:
            assert secret not in line
        assert "token" not in line
