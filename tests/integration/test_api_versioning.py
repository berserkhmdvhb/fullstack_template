from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import pytest

from your_service_name.backend.api import build_api_router
from your_service_name.utils.versioning_api import (
    MSG_ERROR_UNSUPPORTED_VERSION,
    SUPPORTED_VERSIONS,
    get_api_version,
)

if TYPE_CHECKING:
    from fastapi import APIRouter


@pytest.mark.integration
def test_build_api_router_includes_configured_version() -> None:
    """
    When an explicit version is provided, the router should mount under /api/{version}.
    """
    explicit: Final[str] = "v1"
    api: APIRouter = build_api_router(prefix="/api", version=explicit)
    assert api.prefix == f"/api/{explicit}"


@pytest.mark.integration
def test_build_api_router_env_driven_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    When no explicit version is given, the router should resolve the version
    via the centralized resolver (environment first, then code default).
    """
    # Simulate an environment-provided API version
    monkeypatch.setenv("API_VERSION", "v1")
    resolved: str = get_api_version()
    assert resolved in SUPPORTED_VERSIONS

    api: APIRouter = build_api_router(prefix="/api", version=None)
    assert api.prefix == f"/api/{resolved}"

    # Clean up for subsequent tests
    monkeypatch.delenv("API_VERSION", raising=False)


@pytest.mark.integration
def test_build_api_router_empty_env_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    An empty API_VERSION env var is treated as "not set": the router resolves to
    the code default ("v1") and mounts under /api/v1 rather than raising. This
    guards the import-time path (backend/main.py), where CI sets API_VERSION="".
    """
    monkeypatch.setenv("API_VERSION", "")

    api: APIRouter = build_api_router(prefix="/api", version=None)
    assert api.prefix == "/api/v1"

    # Clean up for subsequent tests
    monkeypatch.delenv("API_VERSION", raising=False)


@pytest.mark.integration
def test_build_api_router_unsupported_version_raises() -> None:
    """
    Unsupported versions should raise ValueError with an exact message.
    We escape the expected message for regex matching (Ruff-friendly).
    """
    expected = MSG_ERROR_UNSUPPORTED_VERSION.format(version="vX")
    pattern = re.escape(expected)

    with pytest.raises(ValueError, match=pattern):
        _ = build_api_router(prefix="/api", version="vX")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("candidate", "should_be_supported"),
    [
        ("v1", True),
        ("vX", False),
        ("V1", False),  # current normalization requires exact match
    ],
)
def test_supported_versions_contract(
    monkeypatch: pytest.MonkeyPatch, candidate: str, *, should_be_supported: bool
) -> None:
    """
    Contract check for ENV-based resolution against SUPPORTED_VERSIONS.

    - If ENV provides a supported version, build_api_router() without an explicit
      version should succeed and mount under /api/{candidate}.
    - If ENV provides an unsupported version, build_api_router() should raise.
    """
    # Ensure a clean slate
    monkeypatch.delenv("API_VERSION", raising=False)
    monkeypatch.setenv("API_VERSION", candidate)

    if should_be_supported:
        assert candidate in SUPPORTED_VERSIONS
        api: APIRouter = build_api_router(prefix="/api", version=None)
        assert api.prefix == f"/api/{candidate}"
    else:
        # For env-driven invalids, build_api_router(None) should raise
        # because resolved version is not in SUPPORTED_VERSIONS.
        with pytest.raises(
            ValueError, match=re.escape(MSG_ERROR_UNSUPPORTED_VERSION.format(version=candidate))
        ):
            _ = build_api_router(prefix="/api", version=None)

    # Cleanup
    monkeypatch.delenv("API_VERSION", raising=False)
