# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Tests for API versioning utility.

Covers:
- get_api_version(): env API_VERSION if valid, else DEFAULT_API_VERSION
- Raises ValueError for unsupported versions
- Strips whitespace; empty/whitespace treated as unset -> DEFAULT_API_VERSION
"""

from __future__ import annotations

import pytest

from your_service_name.utils.versioning_api import (
    DEFAULT_API_VERSION,
    MSG_ERROR_UNSUPPORTED_VERSION,
    SUPPORTED_VERSIONS,
    get_api_version,
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


# ---------------------------------------------------------------------------
# Tests: get_api_version() - default behavior
# ---------------------------------------------------------------------------


class TestGetApiVersionDefault:
    """Test get_api_version() default behavior."""

    def test_returns_default_when_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return DEFAULT_API_VERSION when API_VERSION not set."""
        monkeypatch.delenv("API_VERSION", raising=False)

        version = get_api_version()

        assert version == "v1"

    def test_returns_default_when_env_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return DEFAULT_API_VERSION when API_VERSION is empty."""
        monkeypatch.setenv("API_VERSION", "")

        assert get_api_version() == DEFAULT_API_VERSION

    def test_returns_default_when_env_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return DEFAULT_API_VERSION when API_VERSION is whitespace."""
        monkeypatch.setenv("API_VERSION", "   ")

        assert get_api_version() == DEFAULT_API_VERSION


# ---------------------------------------------------------------------------
# Tests: get_api_version() - valid env values
# ---------------------------------------------------------------------------


class TestGetApiVersionValid:
    """Test get_api_version() with valid env values."""

    def test_returns_v1_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return 'v1' when API_VERSION=v1."""
        monkeypatch.setenv("API_VERSION", "v1")

        version = get_api_version()

        assert version == "v1"

    def test_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should strip whitespace from API_VERSION."""
        monkeypatch.setenv("API_VERSION", "  v1  ")

        version = get_api_version()

        assert version == "v1"


# ---------------------------------------------------------------------------
# Tests: get_api_version() - invalid env values
# ---------------------------------------------------------------------------


class TestGetApiVersionInvalid:
    """Test get_api_version() with invalid env values."""

    def test_raises_for_unsupported_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should raise ValueError for unsupported version."""
        monkeypatch.setenv("API_VERSION", "v2")

        with pytest.raises(ValueError, match="Unsupported") as exc_info:
            get_api_version()

        assert "v2" in str(exc_info.value)

    def test_error_message_contains_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Error message should contain the invalid version."""
        monkeypatch.setenv("API_VERSION", "v99")

        with pytest.raises(ValueError, match="Unsupported") as exc_info:
            get_api_version()

        assert "v99" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Tests: get_api_version() - custom env key
# ---------------------------------------------------------------------------


class TestGetApiVersionCustomKey:
    """Test get_api_version() with custom environment variable key."""

    def test_custom_env_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should use custom env key when provided."""
        monkeypatch.setenv("CUSTOM_API_VERSION", "v1")
        monkeypatch.delenv("API_VERSION", raising=False)

        version = get_api_version(env_key="CUSTOM_API_VERSION")

        assert version == "v1"

    def test_custom_env_key_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should return default when custom env key not set."""
        monkeypatch.delenv("CUSTOM_API_VERSION", raising=False)
        monkeypatch.delenv("API_VERSION", raising=False)

        version = get_api_version(env_key="CUSTOM_API_VERSION")

        assert version == "v1"
