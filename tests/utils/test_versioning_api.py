# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for the API versioning constants.

Resolving the ``API_VERSION`` variable is covered with the other settings in
``tests/backend/test_settings.py``.
"""

from __future__ import annotations

from your_service_name.utils.versioning_api import (
    DEFAULT_API_VERSION,
    MSG_ERROR_UNSUPPORTED_VERSION,
    SUPPORTED_VERSIONS,
)


class TestConstants:
    """Test module constants."""

    def test_supported_versions_contains_v1(self) -> None:
        """SUPPORTED_VERSIONS should contain v1."""
        assert "v1" in SUPPORTED_VERSIONS

    def test_default_api_version_is_v1(self) -> None:
        """DEFAULT_API_VERSION should be 'v1'."""
        assert DEFAULT_API_VERSION == "v1"

    def test_error_message_format(self) -> None:
        """MSG_ERROR_UNSUPPORTED_VERSION should be a format string."""
        assert "{version}" in MSG_ERROR_UNSUPPORTED_VERSION
