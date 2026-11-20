"""
Utility helpers shared across the service.

Responsibilities
----------------
- Provide small, import-safe helpers used by multiple packages.
- Keep validation and version resolution centralized and consistent.

Subpackages & Modules
---------------------
- `.validators` — Logging and configuration validators.
- `.versioning` — API version resolution (environment + code default).

Public API
----------
Re-exported for convenience:

- `LogLevel` — Enum of logging levels.
- `get_api_version` — Resolve the HTTP API version.

Operational
-----------
- Concurrency: All helpers are pure functions or constants and are thread-safe.
- I/O: None; no file reads at import time.

Usage
-----
    from your_service_name.utils import LogLevel, get_api_version

    level = LogLevel.resolve("debug")
    api_v = get_api_version()  # -> "v1"
"""

from __future__ import annotations

from .validators import LogLevel
from .versioning_api import get_api_version

__all__ = ["LogLevel", "get_api_version"]
