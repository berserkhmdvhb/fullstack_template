# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Resolve and validate the HTTP API version used to build route prefixes.

Responsibilities
----------------
- Provide a single, import-safe helper to determine the API version.
- Centralize the allow-list of supported versions and the canonical error message.

Public API
----------
- `SUPPORTED_VERSIONS`: Mapping of version -> tuple of router names.
- `MSG_ERROR_UNSUPPORTED_VERSION`: Canonical error message for invalid versions.
- `DEFAULT_API_VERSION`: Code-default version string.
- `get_api_version`: Resolve the API version using env or the code default.

Operational
-----------
- Concurrency: Safe; pure lookups and constants.
- I/O: None (no file reads). Only reads from `os.environ`.
- Logging: None.

Usage
-----
    from your_service_name.utils.versioning_api import (
        SUPPORTED_VERSIONS,
        MSG_ERROR_UNSUPPORTED_VERSION,
        DEFAULT_API_VERSION,
        get_api_version,
    )

    version = get_api_version()             # -> "v1"
    prefix = f"/api/{version}"

Notes
-----
- This module is the single source of truth for API versioning across the service.
- The application/library version (package metadata) remains in `your_service_name.__init__`.
"""

from __future__ import annotations

import os
from typing import Final

__all__ = [
    "API_CONTRACT_VERSION",
    "DEFAULT_API_VERSION",
    "MSG_ERROR_UNSUPPORTED_VERSION",
    "SUPPORTED_VERSIONS",
    "get_api_version",
]

# Single source of truth for API version policy
SUPPORTED_VERSIONS: Final[dict[str, tuple[str, ...]]] = {
    "v1": ("health", "echo", "items"),
}

MSG_ERROR_UNSUPPORTED_VERSION: Final[str] = "[API] Unsupported API version requested: {version}"

# Code-default used when environment does not specify a version
DEFAULT_API_VERSION: Final[str] = "v1"

# OpenAPI / API *contract* version — the version of the API description (OpenAPI ``info.version``),
# not the deployable's build version. Bump it when the API contract changes (endpoints, schemas).
# Deliberately decoupled from the Git-derived package version (``your_service_name.__version__`` via
# setuptools_scm) so the committed OpenAPI snapshot stays a stable, reviewable artifact instead of
# churning on every commit, and from the routing path version (``DEFAULT_API_VERSION``, e.g. "v1")
# so the contract can iterate (1.0.0 -> 1.1.0) under a stable path.
API_CONTRACT_VERSION: Final[str] = "1.0.0"


def get_api_version(env_key: str = "API_VERSION") -> str:
    """
    Resolve the HTTP API version in a single place (import-safe; no file I/O).

    Resolution order
    ----------------
    1) Environment variable `API_VERSION` (if present and valid).
    2) `DEFAULT_API_VERSION` (code default).

    Parameters
    ----------
    env_key : str, optional
        Environment variable name to probe. Defaults to `"API_VERSION"`.

    Returns
    -------
    str
        A version string like `"v1"`.

    Raises
    ------
    ValueError
        If an environment-provided version is not supported.

    Notes
    -----
    - This function does **not** read `.env`; load that in your entrypoint or app bootstrap.
    - Keep imports lightweight and side-effect free at module import time.
    """
    candidate = os.environ.get(env_key)
    # Empty/whitespace env var is treated as "not set" → fall back to default.
    # Only a non-empty value is validated against the allow-list.
    if candidate is not None:
        v = candidate.strip()
        if v:
            if v in SUPPORTED_VERSIONS:
                return v
            msg: Final[str] = MSG_ERROR_UNSUPPORTED_VERSION.format(version=v)
            raise ValueError(msg)

    # Absent or empty env var -> fallback to code default
    return DEFAULT_API_VERSION
