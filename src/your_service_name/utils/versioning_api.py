# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Resolve and validate the HTTP API version used to build route prefixes.

Responsibilities
----------------
- Centralize the allow-list of supported versions and the canonical error message.

Public API
----------
- `SUPPORTED_VERSIONS`: Mapping of version -> tuple of router names.
- `MSG_ERROR_UNSUPPORTED_VERSION`: Canonical error message for invalid versions.
- `DEFAULT_API_VERSION`: Code-default version string.

Operational
-----------
- Concurrency: Safe; pure lookups and constants.
- I/O: None.
- Logging: None.

Usage
-----
    from your_service_name.utils.versioning_api import (
        SUPPORTED_VERSIONS,
        MSG_ERROR_UNSUPPORTED_VERSION,
        DEFAULT_API_VERSION,
    )

    prefix = f"/api/{DEFAULT_API_VERSION}"

Notes
-----
- This module is the single source of truth for API versioning across the service.
- The application/library version (package metadata) remains in `your_service_name.__init__`.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "API_CONTRACT_VERSION",
    "DEFAULT_API_VERSION",
    "MSG_ERROR_UNSUPPORTED_VERSION",
    "SUPPORTED_VERSIONS",
]

# Single source of truth for API version policy
SUPPORTED_VERSIONS: Final[dict[str, tuple[str, ...]]] = {
    "v1": ("health", "echo", "items"),
}

MSG_ERROR_UNSUPPORTED_VERSION: Final[str] = "[API] Unsupported API version requested: {version}"

# Code-default used when the `API_VERSION` setting is unset (the value is validated in
# `your_service_name.backend.settings`)
DEFAULT_API_VERSION: Final[str] = "v1"

# OpenAPI / API *contract* version — the version of the API description (OpenAPI ``info.version``),
# not the deployable's build version. Bump it when the API contract changes (endpoints, schemas).
# Deliberately decoupled from the Git-derived package version (``your_service_name.__version__`` via
# setuptools_scm) so the committed OpenAPI snapshot stays a stable, reviewable artifact instead of
# churning on every commit, and from the routing path version (``DEFAULT_API_VERSION``, e.g. "v1")
# so the contract can iterate (1.0.0 -> 1.1.0) under a stable path.
API_CONTRACT_VERSION: Final[str] = "1.0.0"
