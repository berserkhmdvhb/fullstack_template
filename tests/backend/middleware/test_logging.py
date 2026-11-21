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
    """Test middleware skips health check endpoints."""

    @pytest.mark.asyncio
    async def test_skips_healthz(
        self,
        middleware: RequestLoggingMiddleware,
        mock_call_next: AsyncMock,
    ) -> None:
        """Should skip logging for /healthz."""
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"
        mock_request.url.path = "/healthz"

        with patch("your_service_name.backend.middleware.logging.logger") as mock_logger:
            response = await middleware.dispatch(mock_request, mock_call_next)

            # Should not log
            mock_logger.info.assert_not_called()

            # Should still call next
            mock_call_next.assert_called_once_with(mock_request)

            assert response.status_code == EXPECTED_STATUS_CODE


class TestSkipPathsSet:
    """Test _SKIP_PATHS configuration."""

    def test_skip_paths_contains_healthz(self) -> None:
        """_SKIP_PATHS should contain /healthz."""
        assert "/healthz" in RequestLoggingMiddleware._SKIP_PATHS
