from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import pytest
from pydantic import ValidationError

from your_service_name.backend.api import build_api_router
from your_service_name.backend.settings import Settings
from your_service_name.utils.versioning_api import (
    DEFAULT_API_VERSION,
    MSG_ERROR_UNSUPPORTED_VERSION,
    SUPPORTED_VERSIONS,
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
def test_build_api_router_defaults_to_the_code_default() -> None:
    """Without an explicit version the router mounts under the code default."""
    api: APIRouter = build_api_router(prefix="/api")
    assert api.prefix == f"/api/{DEFAULT_API_VERSION}"


@pytest.mark.integration
def test_settings_version_drives_the_router(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``API_VERSION`` setting (env) is what ``create_app`` mounts the router under."""
    monkeypatch.setenv("API_VERSION", "v1")
    resolved: str = Settings().API_VERSION
    assert resolved in SUPPORTED_VERSIONS

    api: APIRouter = build_api_router(prefix="/api", version=resolved)
    assert api.prefix == f"/api/{resolved}"


@pytest.mark.integration
def test_empty_api_version_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    An empty API_VERSION env var is treated as "not set": the settings resolve to the
    code default ("v1") rather than raising. This guards the import-time path
    (backend/main.py), where CI sets API_VERSION="".
    """
    monkeypatch.setenv("API_VERSION", "")

    assert Settings().API_VERSION == DEFAULT_API_VERSION


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

    - If ENV provides a supported version, the settings accept it and the router mounts
      under /api/{candidate}.
    - If ENV provides an unsupported version, building the settings raises, naming it.
    """
    monkeypatch.setenv("API_VERSION", candidate)

    if should_be_supported:
        assert candidate in SUPPORTED_VERSIONS
        api: APIRouter = build_api_router(prefix="/api", version=Settings().API_VERSION)
        assert api.prefix == f"/api/{candidate}"
    else:
        with pytest.raises(
            ValidationError,
            match=re.escape(MSG_ERROR_UNSUPPORTED_VERSION.format(version=candidate)),
        ):
            Settings()
