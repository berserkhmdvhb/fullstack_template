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
    with pytest.raises(AssertionError, match="template literal"):
        parse_backend_paths(_src("`${A}/x`"))


def test_real_file_resolves_to_paths() -> None:
    assert parse_backend_paths(_REAL.read_text(encoding="utf-8")) == [
        "/api/v1",
        "/docs",
        "/openapi.json",
    ]


def test_trailing_comma_one_line_and_multi_line() -> None:
    assert parse_backend_paths(_src("A, '/b',")) == ["/a", "/b"]
    assert parse_backend_paths(_src("\n  A,\n  '/b',\n")) == ["/a", "/b"]


def test_double_quoted_element_and_constant() -> None:
    src = 'const D = "/d";\nconst BACKEND_PATHS = [D, "/e"];\n'
    assert parse_backend_paths(src) == ["/d", "/e"]


def test_plain_const_form() -> None:
    assert parse_backend_paths("const P = '/p';\nconst BACKEND_PATHS = [P];\n") == ["/p"]


def test_comma_inside_a_quoted_literal() -> None:
    assert parse_backend_paths(_src("'/a,b', A")) == ["/a,b", "/a"]


def test_backtick_literal_is_refused_even_without_interpolation() -> None:
    with pytest.raises(AssertionError, match="template literal"):
        parse_backend_paths(_src("`/x`"))


def test_unparseable_element_fails() -> None:
    with pytest.raises(AssertionError, match="cannot parse"):
        parse_backend_paths(_src("A + '/x'"))


def test_empty_array_fails() -> None:
    with pytest.raises(AssertionError, match="empty"):
        parse_backend_paths(_src(""))
