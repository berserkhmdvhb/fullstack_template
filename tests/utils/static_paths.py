"""Read `BACKEND_PATHS` out of frontend/static-paths.ts (text parse, no Node)."""

from __future__ import annotations

import re


def parse_backend_paths(source: str) -> list[str]:
    """`BACKEND_PATHS` with each identifier replaced by its `const NAME = '<literal>'`."""
    listed = re.search(r"BACKEND_PATHS[^=]*= \[([^\]]+)\]", source)
    assert listed, "static-paths.ts no longer defines BACKEND_PATHS"
    consts = dict(
        re.findall(r"^(?:export )?const (\w+)\s*(?::[^=]+)?= '([^']*)';", source, re.MULTILINE)
    )
    paths = []
    for item in (item.strip() for item in listed.group(1).split(",")):
        quoted = re.fullmatch(r"'([^']*)'|\"([^\"]*)\"", item)
        if quoted:
            paths.append(quoted.group(1) if quoted.group(1) is not None else quoted.group(2))
        elif re.fullmatch(r"[A-Za-z_]\w*", item):
            assert item in consts, (
                f"BACKEND_PATHS uses {item}, not a string literal in static-paths.ts"
            )
            paths.append(consts[item])
        else:
            msg = f"BACKEND_PATHS element {item!r} is neither a quoted literal nor a constant"
            raise AssertionError(msg)
    assert paths, "BACKEND_PATHS is empty"
    assert all(p.startswith("/") for p in paths), f"BACKEND_PATHS read as {paths}: not all paths"
    return paths
