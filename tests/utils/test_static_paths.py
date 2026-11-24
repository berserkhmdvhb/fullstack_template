"""The static-paths.ts reader used by the live suite, tested without a server."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.utils.static_paths import parse_backend_paths

_REAL = Path(__file__).resolve().parents[2] / "frontend" / "static-paths.ts"


def _src(body: str) -> str:
    return f"export const A = '/a';\nexport const BACKEND_PATHS: readonly string[] = [{body}];\n"


def test_identifier_is_resolved() -> None:
    assert parse_backend_paths(_src("A, '/b'")) == ["/a", "/b"]


def test_quoted_literal() -> None:
    assert parse_backend_paths(_src("'/x'")) == ["/x"]


def test_unresolved_identifier_fails_naming_it() -> None:
    with pytest.raises(AssertionError, match="MISSING"):
        parse_backend_paths(_src("MISSING"))


def test_template_literal_fails() -> None:
    with pytest.raises(AssertionError, match="neither a quoted literal"):
        parse_backend_paths(_src("`${A}/x`"))


def test_real_file_resolves_to_paths() -> None:
    assert parse_backend_paths(_REAL.read_text(encoding="utf-8")) == [
        "/api/v1",
        "/docs",
        "/openapi.json",
    ]
