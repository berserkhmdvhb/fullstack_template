"""
Utility helpers shared across the service.

Responsibilities
----------------
- Provide small, import-safe helpers used by multiple packages.
- Keep validation and version resolution centralized and consistent.

Subpackages & Modules
---------------------
- `.validators` — Logging and configuration validators.
- `.versioning_api` — API version allow-list and code default.

Public API
----------
Re-exported for convenience:

- `LogLevel` — Enum of logging levels.

Operational
-----------
- Concurrency: All helpers are pure functions or constants and are thread-safe.
- I/O: None; no file reads at import time.

Usage
-----
    from your_service_name.utils import LogLevel

    level = LogLevel.resolve("debug")
"""

from __future__ import annotations

from .validators import LogLevel

__all__ = ["LogLevel"]
