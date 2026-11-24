"""Read `BACKEND_PATHS` out of frontend/static-paths.ts (text parse, no Node)."""

from __future__ import annotations

import re


def parse_backend_paths(source: str) -> list[str]:
    """`BACKEND_PATHS` with each identifier replaced by its `const NAME = '<literal>'`."""
    listed = re.search(r"BACKEND_PATHS[^=]*= \[([^\]]*)\]", source)
    assert listed, "static-paths.ts no longer defines BACKEND_PATHS"
    consts = {
        m[0]: m[2] if m[1] == "'" else m[3]
        for m in re.findall(
            r"""^(?:export )?const (\w+)\s*(?::[^=]+)?= (?:(')([^']*)'|"([^"]*)");""",
            source,
            re.MULTILINE,
        )
    }
    element = r"""'[^']*'|"[^"]*"|`[^`]*`|[A-Za-z_]\w*"""
    body = listed.group(1)
    leftover = re.sub(element, "", body).replace(",", "").strip()
    assert not leftover, f"BACKEND_PATHS has elements the reader cannot parse: {leftover!r}"
    paths = []
    for item in re.findall(element, body):
        if item[0] in "'\"":
            paths.append(item[1:-1])
        elif item[0] == "`":
            msg = f"BACKEND_PATHS element {item} is a template literal, not a string constant"
            raise AssertionError(msg)
        else:
            assert item in consts, (
                f"BACKEND_PATHS uses {item}, not a string literal in static-paths.ts"
            )
            paths.append(consts[item])
    assert paths, "BACKEND_PATHS is empty"
    assert all(p.startswith("/") for p in paths), f"BACKEND_PATHS read as {paths}: not all paths"
    return paths
