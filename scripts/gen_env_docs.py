#!/usr/bin/env python3
"""Generate the backend-settings reference in wiki/environment-variables.md from ``Settings``.

  scripts/gen_env_docs.py            rewrite the generated block in the doc
  scripts/gen_env_docs.py --check    read-only: exit 1 if the block is stale, 2 if it has no markers

The block between the BEGIN and END markers is a pure function of the ``Settings`` model (names,
types, defaults, descriptions, production rules), never of the environment; everything outside the
markers (the variables read by the frontend, compose and CI) is hand-written and kept as is.
"""

from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Union, get_args, get_origin

from pydantic import SecretStr

from your_service_name.backend.settings import Settings

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo
    from pydantic_settings import BaseSettings

DEFAULT_DOC = Path(__file__).resolve().parent.parent / "wiki" / "environment-variables.md"
BEGIN = (
    "<!-- BEGIN GENERATED: backend settings (scripts/gen_env_docs.py; edit Settings, not this) -->"
)
END = "<!-- END GENERATED -->"

HEADER = """\
## Backend settings

Every variable the Python backend reads is declared once, in `Settings`
(`src/your_service_name/backend/settings.py`), and validated at startup: an invalid value stops
the backend with one error naming each problem. Names are case-sensitive.

`ENV` selects the deployment environment. When it is `production` (or `prod`, case-insensitive) the
rules in the last column are enforced at startup, and a violation stops the backend. Any other value
leaves them off, and an unset `ENV` is `dev`. The chart's production values set `ENV=production`, so
the rules are on there.

`TRUSTED_PROXY_CIDRS` and `TRUST_CF_CONNECTING_IP` decide which forwarding headers the backend
believes when it resolves the client IP; the model is in [Client IP](security.md#client-ip).
"""


def _type_name(field: FieldInfo) -> str:
    """A short, human type for a field (``str``, ``bool``, ``list of str``, ``secret``...)."""
    annotation = field.annotation
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin in (Union, types.UnionType):
        args = tuple(a for a in args if a is not type(None))
        if args == (SecretStr,):
            return "secret"
        annotation = args[0]
        origin, args = get_origin(annotation), get_args(annotation)
    if origin is Literal:
        return "one of " + ", ".join(f"`{a}`" for a in args)
    if origin is list:
        return "comma-separated list"
    return getattr(annotation, "__name__", str(annotation))


def _default(field: FieldInfo) -> str:
    """The documented default: secrets are never shown, empty/unset are spelled out."""
    if "secret" in _type_name(field).lower():
        return "(secret)"
    value = field.get_default(call_default_factory=True)
    if isinstance(value, list):
        return f"`{','.join(value)}`" if value else "(empty)"
    if value is None:
        return "(unset)"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    return f"`{value}`" if value != "" else "(empty)"


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render(model: type[BaseSettings]) -> str:
    """The generated block (markers included) for ``model``; deterministic and env-independent."""
    rows = []
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra
        rule = extra.get("production", "—") if isinstance(extra, dict) else "—"
        cells = [f"`{name}`", _type_name(field), _default(field)]
        cells += [_cell(field.description or ""), _cell(str(rule))]
        rows.append("| " + " | ".join(cells) + " |")
    table = [
        "| Variable | Type | Default | Description | Production rule |",
        "|---|---|---|---|---|",
    ]
    return "\n".join([BEGIN, "", HEADER, *table, *rows, "", END]) + "\n"


def splice(text: str, block: str) -> str:
    """Replace the generated block in ``text``; ``ValueError`` if the markers are missing."""
    start, stop = text.find(BEGIN), text.find(END)
    if start < 0 or stop < start:
        msg = "the BEGIN/END generated-block markers are missing"
        raise ValueError(msg)
    return text[:start] + block.rstrip("\n") + text[stop + len(END) :]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the backend-settings env-var reference.")
    parser.add_argument("--check", action="store_true", help="fail if the doc is stale")
    parser.add_argument("--doc", type=Path, default=DEFAULT_DOC, help="the doc to update")
    args = parser.parse_args(argv)

    current = args.doc.read_text(encoding="utf-8")
    try:
        wanted = splice(current, render(Settings))
    except ValueError as exc:
        sys.stderr.write(f"{args.doc}: {exc}\n")
        return 2
    if wanted == current:
        return 0
    if args.check:
        sys.stderr.write(f"{args.doc} is out of date with Settings; run: make env-docs\n")
        return 1
    args.doc.write_text(wanted, encoding="utf-8")
    sys.stdout.write(f"updated {args.doc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
