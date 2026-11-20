# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

import logging

from your_service_name.utils.validators import LogLevel


def test_log_level_resolve_basic_names() -> None:
    assert LogLevel.resolve("info") == logging.INFO
    assert LogLevel.resolve(" DEBUG ") == logging.DEBUG
    assert LogLevel.resolve("ERROR") == logging.ERROR
    assert LogLevel.resolve("warn") == logging.WARNING  # WARN -> WARNING mapping


def test_log_level_resolve_numeric_strings_and_fallback() -> None:
    # numeric value that matches an Enum member value
    assert LogLevel.resolve("20") == logging.INFO

    # unknown token falls back to default when provided
    assert LogLevel.resolve("nope", default=LogLevel.ERROR) == logging.ERROR

    # unknown token falls back to INFO when default is None
    assert LogLevel.resolve("nope") == logging.INFO


def test_log_level_resolve_empty_uses_default() -> None:
    assert LogLevel.resolve("", default=LogLevel.DEBUG) == logging.DEBUG
    assert LogLevel.resolve(None) == logging.INFO  # default is INFO when not provided
