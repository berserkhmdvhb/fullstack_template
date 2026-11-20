# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Single source export for the HTTP OpenAPI document.

The committed ``frontend/lib/api/openapi.json`` is a *generated artifact*: a snapshot of
``FastAPI.openapi()`` for the assembled application — never hand-edited. Regenerating it
from the backend (the one true source of the API contract) is the only supported way to
refresh it, so the frontend's auto-generated TypeScript types can never silently drift
from what the backend actually serves (finding C1 + the OpenAPI drift gate).

Usage
-----
    python -m your_service_name.backend.openapi dump --output frontend/lib/api/openapi.json

The matching Make target (``openapi-gen``) regenerates the snapshot and then the frontend
types; CI runs the same dump and fails the pipeline if the committed snapshot (or the
generated ``types.gen.d.ts``) is out of date.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from your_service_name.backend.main import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Where the committed OpenAPI snapshot lives, relative to the project root. Single source
# for the frontend's typed API client (the openapi-typescript input).
FRONTEND_OPENAPI_REL_PATH: str = "frontend/lib/api/openapi.json"


def build_openapi_schema() -> dict[str, Any]:
    """
    Return the OpenAPI schema for the assembled application.

    Notes
    -----
    - The app is assembled fresh via ``create_app()`` and ``FastAPI.openapi()`` is pure
      route/schema introspection — it does not start a server or touch the database, so
      this is safe to call from a generation/CI step with no runtime environment.
    """
    app: FastAPI = create_app()
    return app.openapi()


def dump(output: Path) -> None:
    """
    Write the OpenAPI schema to *output* as pretty, key-sorted JSON.

    Sorting keys keeps the committed snapshot a stable, reviewable diff rather than a
    churny one that reorders on every regeneration.
    """
    schema = build_openapi_schema()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``python -m your_service_name.backend.openapi``."""
    parser = argparse.ArgumentParser(
        prog="python -m your_service_name.backend.openapi",
        description="Export the backend OpenAPI document (single source of the API contract).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    dump_parser = sub.add_parser("dump", help="Write the OpenAPI schema to --output.")
    dump_parser.add_argument(
        "--output",
        type=Path,
        default=Path(FRONTEND_OPENAPI_REL_PATH),
        help="Destination path for the OpenAPI JSON snapshot (relative to cwd).",
    )

    args = parser.parse_args(argv)
    if args.command == "dump":
        dump(args.output)
        logger.info("Wrote OpenAPI schema to %s", args.output)
        return 0
    return 1  # pragma: no cover - argparse 'required=True' makes this unreachable


if __name__ == "__main__":
    raise SystemExit(main())
