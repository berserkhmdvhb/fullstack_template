# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Validation helpers and typed utility enums.

Responsibilities
----------------
- Provide small, focused validators used across settings and bootstrapping.
- Map human-friendly inputs to canonical, typed representations.

Public API
----------
- `LogLevel`: Enum of logging levels.
- `LogLevel.resolve`: Resolve a user-provided string/number to a numeric level.

Operational
-----------
- Concurrency: Safe; pure functions and constants.
- I/O: None.

Usage
-----
    from your_service_name.utils.validators import LogLevel

    level = LogLevel.resolve(" warn ")      # -> logging.WARNING (30)
    level = LogLevel.resolve("20")          # -> logging.INFO (if 20 equals INFO)
    level = LogLevel.resolve(None)          # -> logging.INFO (default)
"""

from __future__ import annotations

import logging
from enum import Enum

__all__: list[str] = ["LogLevel"]


class LogLevel(Enum):
    """
    Enumeration of standard logging levels.

    Extended Summary
    ----------------
    Values mirror `logging` numeric levels. Use `resolve` to translate
    user-provided configuration values (strings or numeric strings) into
    canonical integers for `basicConfig(level=...)`.

    Attributes
    ----------
    CRITICAL : int
        Numeric level `50`.
    ERROR : int
        Numeric level `40`.
    WARNING : int
        Numeric level `30`.
    INFO : int
        Numeric level `20`.
    DEBUG : int
        Numeric level `10`.
    NOTSET : int
        Numeric level `0`.
    """

    CRITICAL = logging.CRITICAL
    ERROR = logging.ERROR
    WARNING = logging.WARNING
    INFO = logging.INFO
    DEBUG = logging.DEBUG
    NOTSET = logging.NOTSET

    @classmethod
    def resolve(cls, value: str | None, default: LogLevel | None = None) -> int:
        """
        Resolve a string like `"info"`, `" warn "`, `"DEBUG"` (or a numeric string)
        to a numeric logging level.

        Parameters
        ----------
        value : str or None
            Input value to normalize. May be a level name, a numeric string (e.g., `"20"`),
            or `None`.
        default : LogLevel or None, optional
            Default enum member used when `value` is `None` or unrecognized.
            If omitted, defaults to `LogLevel.INFO`.

        Returns
        -------
        int
            Numeric logging level suitable for `logging.basicConfig(level=...)`.

        Notes
        -----
        - Trims whitespace and uppercases names.
        - Maps `"WARN"` → `"WARNING"` for convenience.
        - If a numeric string is supplied (e.g., `"20"`), it is returned as-is
          **iff** it matches an existing enum value; otherwise `default` is used.
        """
        # Avoid binding a mutable default at definition time
        if default is None:
            default = cls.INFO

        if not value:
            return default.value

        name = value.strip().upper()

        if name == "WARN":
            name = "WARNING"

        # Accept numeric strings, but only if they equal a known level
        if name.isdigit():
            as_int = int(name)
            for member in cls:
                if member.value == as_int:
                    return as_int
            return default.value

        try:
            return cls[name].value
        except KeyError:
            return default.value
