# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Tests for the OpenAPI single-source export (backend/openapi.py).

The committed ``frontend/lib/api/openapi.json`` is a generated snapshot of
``FastAPI.openapi()``; these tests pin the export contract (pretty, key-sorted
JSON written to disk, and the CLI ``dump`` entry point) so the single source of
the API contract cannot silently change shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from your_service_name.backend.main import create_app
from your_service_name.backend.openapi import build_openapi_schema, dump, main


def test_build_openapi_schema_has_the_openapi_contract_shape() -> None:
    schema = build_openapi_schema()

    # Standard OpenAPI top-level keys.
    assert schema["openapi"].startswith("3.")
    assert isinstance(schema["info"], dict)
    assert isinstance(schema["paths"], dict)
    # The assembled app exposes the versioned health/echo API.
    assert any("/health" in path for path in schema["paths"])


def test_build_openapi_schema_matches_the_running_app() -> None:
    # build_openapi_schema() must be the assembled app's schema, byte-for-byte —
    # that is the single-source guarantee the dump command relies on.
    expected = create_app().openapi()
    assert build_openapi_schema() == expected


def test_dump_writes_pretty_key_sorted_json(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "openapi.json"
    dump(out)

    text = out.read_text(encoding="utf-8")
    schema = build_openapi_schema()

    # Pretty (indent=2), trailing newline, and key-sorted => a stable diff.
    assert text == json.dumps(schema, indent=2, sort_keys=True) + "\n"
    # And it round-trips back to the same schema.
    assert json.loads(text) == schema


def test_dump_is_deterministic(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    dump(a)
    dump(b)
    # Same input schema => identical bytes (key-sorted), regardless of path.
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")


def test_main_dump_writes_to_the_requested_output(tmp_path: Path) -> None:
    out = tmp_path / "openapi.json"

    rc = main(["dump", "--output", str(out)])

    assert rc == 0
    assert out.exists()
    # The CLI writes the same key-sorted snapshot dump() does.
    schema = build_openapi_schema()
    assert json.loads(out.read_text(encoding="utf-8")) == schema
